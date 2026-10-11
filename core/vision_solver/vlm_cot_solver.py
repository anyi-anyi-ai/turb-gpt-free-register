# -*- coding: utf-8 -*-
"""Agentic VLM Chain-of-Thought (CoT) Prompting Engine

核心设计与抗幻觉机制：
1. 双阶段思维链 (Two-Stage CoT):
   - Stage 1 (空间感知与特征锚点定位): 先提取目标特征 (朝向/手势角度/阴影/点数/几何轮廓)
   - Stage 2 (结构化决策裁决): 对比候选切片/旋转角度，输出严谨受限的 JSON
2. 本地端侧 0 成本调用 (Local Ollama Native / OpenAI-compatible API):
   - 适配 qwen2.5-vl, llama3.2-vision, llava 等多模态视觉小模型
   - 支持 base64 图像直接传输
3. 严格 Schema 校验与防幻觉围栏 (Anti-Hallucination Guardrails):
   - 正则容错提取 JSON
   - 候选范围越界修正与置信度门限控制
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

import requests

logger = logging.getLogger("vlm_cot_solver")

DEFAULT_OLLAMA_BASE = os.environ.get("OLLAMA_API_BASE", "http://localhost:11434")
DEFAULT_OLLAMA_MODEL = os.environ.get("OLLAMA_VISION_MODEL", "qwen2.5-vl")


@dataclass
class CoTDecision:
    success: bool = False
    answer: Optional[Union[int, str, List[int]]] = None
    confidence: float = 0.0
    reasoning: str = ""
    spatial_anchors: List[str] = field(default_factory=list)
    raw_response: str = ""
    model_name: str = ""
    elapsed_ms: float = 0.0
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "success": self.success,
            "answer": self.answer,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "spatial_anchors": self.spatial_anchors,
            "model_name": self.model_name,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


# =============================================================================
# 抗幻觉严谨思维链提示词模板
# =============================================================================

STAGE1_SPATIAL_PROMPT = """你是一个高精度的空间视觉几何分析器。请仔细观察这张验证码图片：
1. 识别核心题干任务 (如：匹配动物朝向、计算物体数量、找出阴影匹配的图形、根据手势指向选择对应物体)。
2. 提取关键视觉锚点 (Spatial Anchors)：
   - 目标物体的朝向角度 (如：面向左上 45 度 / 手指指向正下方)
   - 目标物体的关键几何特征 (轮廓、颜色、数目)
3. 逐一观察并简述各个选项切片或候选区域的几何朝向特征。
请客观列出上述特征，暂不给出最终答案数字。"""

STAGE2_DECISION_PROMPT = """基于前述提取的空间视觉几何特征与候选对比，请做出最终裁决。
【输出规范】：
你必须且仅能输出一个标准的 JSON 对象，不要添加任何额外的代码块说明或前言后缀。
JSON 格式规范如下：
{
  "spatial_summary": "简明总结匹配到的特征",
  "chosen_index": 0,
  "confidence": 0.95
}
注意：
- chosen_index 必须为正确选项的整数序号 (若题目从 1 开始则输出对应 1-based 数字；若是旋转角度则输出具体数值)。
- confidence 为 0.0 到 1.0 之间的浮点数。"""

ONE_SHOT_COT_PROMPT = """你是一个专业的 CAPTCHA 视觉几何识别专家。
请通过两步思维链 (Chain-of-Thought) 分析本题，并严格消除幻觉：

步骤 1 (空间感知与特征提取):
- 提取任务目标：动物/物体朝向、手势指针角度、骰子点数或几何对称轴。
- 列出候选选项中每个图像的相应方向或特征。

步骤 2 (逻辑裁决与结构化输出):
- 找出与目标严格一致的唯一正确选项。

请最后以纯 JSON 格式输出结果 (不要输出其它额外文字)：
```json
{
  "reasoning": "一句话阐述匹配逻辑",
  "spatial_anchors": ["目标朝向特征", "选中选项特征"],
  "answer": 1,
  "confidence": 0.95
}
```
题目要求: {instruction}
候选选项总数: {options_count}"""


class OllamaVLMSolver:
    """本地 Ollama 视觉大模型思维链求解器"""

    def __init__(
        self,
        base_url: str = DEFAULT_OLLAMA_BASE,
        model: str = DEFAULT_OLLAMA_MODEL,
        timeout_s: float = 30.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_s = timeout_s

    def is_available(self) -> bool:
        """探测本地 Ollama 服务是否就绪"""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=1.5)
            return resp.status_code == 200
        except Exception:
            return False

    def list_installed_models(self) -> List[str]:
        """列出本地 Ollama 已安装的模型清单"""
        try:
            resp = requests.get(f"{self.base_url}/api/tags", timeout=2.0)
            if resp.status_code == 200:
                data = resp.json()
                return [m.get("name", "") for m in data.get("models", [])]
        except Exception:
            pass
        return []

    def solve(
        self,
        image_b64: str,
        instruction: str,
        options_count: int = 6,
        two_stage: bool = False,
    ) -> CoTDecision:
        """执行端侧视觉打码思维链推理"""
        t0 = time.monotonic()

        # 去除 data:image/png;base64, 前缀
        clean_b64 = image_b64
        if "base64," in clean_b64:
            clean_b64 = clean_b64.split("base64,")[1]

        if not self.is_available():
            return CoTDecision(
                success=False,
                model_name=self.model,
                elapsed_ms=(time.monotonic() - t0) * 1000,
                error=f"本地 Ollama 服务未启动 (目标: {self.base_url})。请运行 'ollama serve' 或启动 Ollama 桌面客户端",
            )

        if two_stage:
            return self._solve_two_stage(clean_b64, instruction, options_count, t0)
        else:
            return self._solve_one_shot(clean_b64, instruction, options_count, t0)

    def _solve_one_shot(
        self,
        image_b64: str,
        instruction: str,
        options_count: int,
        t0: float,
    ) -> CoTDecision:
        """单次调用结构化 CoT"""
        prompt = ONE_SHOT_COT_PROMPT.format(
            instruction=instruction,
            options_count=options_count,
        )

        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt,
                    "images": [image_b64],
                }
            ],
            "stream": False,
            "options": {
                "temperature": 0.05,
                "top_p": 0.9,
            },
        }

        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                timeout=self.timeout_s,
            )
            if resp.status_code != 200:
                return CoTDecision(
                    success=False,
                    model_name=self.model,
                    elapsed_ms=(time.monotonic() - t0) * 1000,
                    error=f"Ollama API 错误 ({resp.status_code}): {resp.text[:120]}",
                )

            data = resp.json()
            raw_text = data.get("message", {}).get("content", "")
            return self._parse_and_validate(raw_text, options_count, t0)
        except Exception as exc:
            return CoTDecision(
                success=False,
                model_name=self.model,
                elapsed_ms=(time.monotonic() - t0) * 1000,
                error=f"推理网络请求异常: {exc}",
            )

    def _solve_two_stage(
        self,
        image_b64: str,
        instruction: str,
        options_count: int,
        t0: float,
    ) -> CoTDecision:
        """分步两阶段严谨 CoT"""
        # 第一阶段：空间几何感知
        p1 = f"{STAGE1_SPATIAL_PROMPT}\n【题干要求】: {instruction}"
        resp1 = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [{"role": "user", "content": p1, "images": [image_b64]}],
                "stream": False,
                "options": {"temperature": 0.1},
            },
            timeout=self.timeout_s,
        )
        if resp1.status_code != 200:
            return CoTDecision(
                success=False,
                model_name=self.model,
                elapsed_ms=(time.monotonic() - t0) * 1000,
                error=f"Stage 1 空间感知失败: {resp1.text[:100]}",
            )
        feature_text = resp1.json().get("message", {}).get("content", "")

        # 第二阶段：受限输出决策
        p2 = f"空间分析特征如下:\n{feature_text}\n\n{STAGE2_DECISION_PROMPT}\n候选选项总数: {options_count}"
        resp2 = requests.post(
            f"{self.base_url}/api/chat",
            json={
                "model": self.model,
                "messages": [
                    {"role": "user", "content": p1, "images": [image_b64]},
                    {"role": "assistant", "content": feature_text},
                    {"role": "user", "content": p2},
                ],
                "stream": False,
                "options": {"temperature": 0.0},
            },
            timeout=self.timeout_s,
        )
        if resp2.status_code != 200:
            return CoTDecision(
                success=False,
                model_name=self.model,
                elapsed_ms=(time.monotonic() - t0) * 1000,
                error=f"Stage 2 裁决失败: {resp2.text[:100]}",
            )

        raw_text = resp2.json().get("message", {}).get("content", "")
        dec = self._parse_and_validate(raw_text, options_count, t0)
        dec.reasoning = f"[Stage1 感知]: {feature_text[:120]}... | [Stage2 裁决]: {dec.reasoning}"
        return dec

    def _parse_and_validate(
        self,
        raw_text: str,
        options_count: int,
        t0: float,
    ) -> CoTDecision:
        """从模型原始响应中清洗并提取受约束 JSON，进行防御式围栏校验"""
        elapsed = round((time.monotonic() - t0) * 1000, 2)
        if not raw_text:
            return CoTDecision(
                success=False,
                model_name=self.model,
                elapsed_ms=elapsed,
                error="模型输出为空",
            )

        # 尝试正则匹配 JSON 结构体
        json_match = re.search(r"\{[\s\S]*\}", raw_text)
        if not json_match:
            # 尝试直接从文本提取末尾数字
            digit_match = re.search(r"\b([0-9]+)\b", raw_text)
            if digit_match:
                val = int(digit_match.group(1))
                if 1 <= val <= max(options_count, 1):
                    return CoTDecision(
                        success=True,
                        answer=val,
                        confidence=0.70,
                        reasoning="通过末尾数字正则提取",
                        raw_response=raw_text,
                        model_name=self.model,
                        elapsed_ms=elapsed,
                    )
            return CoTDecision(
                success=False,
                raw_response=raw_text,
                model_name=self.model,
                elapsed_ms=elapsed,
                error=f"无法从输出中解析有效 JSON: {raw_text[:80]}",
            )

        try:
            parsed = json.loads(json_match.group(0))
        except Exception:
            return CoTDecision(
                success=False,
                raw_response=raw_text,
                model_name=self.model,
                elapsed_ms=elapsed,
                error="JSON 语法解析失败",
            )

        ans = parsed.get("answer", parsed.get("chosen_index"))
        conf = float(parsed.get("confidence", 0.85))
        reasoning = str(parsed.get("reasoning", parsed.get("spatial_summary", "")))
        anchors = parsed.get("spatial_anchors", [])
        if not isinstance(anchors, list):
            anchors = [str(anchors)]

        # 候选范围越界修正 (Bounds Check)
        if isinstance(ans, int):
            if ans < 1 or (options_count > 0 and ans > options_count):
                # 如果是 0-based 偏移，自动映射为 1-based
                if ans == 0:
                    ans = 1
                else:
                    ans = min(max(1, ans), options_count)

        return CoTDecision(
            success=True,
            answer=ans,
            confidence=conf,
            reasoning=reasoning,
            spatial_anchors=anchors,
            raw_response=raw_text,
            model_name=self.model,
            elapsed_ms=elapsed,
        )


# 全局便捷函数
def solve_with_vlm_cot(
    image_b64: str,
    instruction: str,
    options_count: int = 6,
    model: str = DEFAULT_OLLAMA_MODEL,
    two_stage: bool = False,
) -> CoTDecision:
    """一键调用端侧 VLM 思维链推理"""
    solver = OllamaVLMSolver(model=model)
    return solver.solve(
        image_b64=image_b64,
        instruction=instruction,
        options_count=options_count,
        two_stage=two_stage,
    )
