# -*- coding: utf-8 -*-
"""自适应多模态打码智能路由器 (Adaptive Captcha Model Router)

分级路由策略：
1. 第一梯队 (专精极速): 命中几何/旋转/网格/滑块特征 -> 路由至本地专精 ONNX 小模型 (15ms, 0 显存, 0 依赖)
2. 第二梯队 (通用端侧): 命中开放式长文本/复杂多模态逻辑 -> 路由至本地 Ollama (Qwen2.5-VL / Llama3.2-Vision)
3. 第三梯队 (声学降维): 视觉阻塞或音频无障碍通道就绪 -> 路由至本地 Whisper (base.en)
4. 第四梯队 (云端多模): 本地未就绪时平滑回退 -> 云端多模型投票 (GPT-4V / Gemini / Claude)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("captcha_router")

MODELS_DIR = Path("f:/github/turb-gpt-free-register/models/captcha").resolve()
REGISTRY_PATH = MODELS_DIR / "registry.json"


@dataclass
class RouteDecision:
    tier: str  # "ONNX_SPECIALIZED" | "OLLAMA_LOCAL" | "WHISPER_AUDIO" | "CLOUD_VOTE"
    model_id: str
    model_name: str
    target_engine: str  # "onnxruntime" | "ollama" | "faster-whisper" | "cloud_gateway"
    confidence: float
    reason: str
    weight_path: Optional[str] = None


class CaptchaModelRouter:
    """自适应验证码打码调度引擎"""

    def __init__(self, registry_file: Optional[Path] = None):
        self.registry_file = registry_file or REGISTRY_PATH
        self.registry = self._load_registry()

    def _load_registry(self) -> Dict[str, Any]:
        if not self.registry_file.is_file():
            return {"models": []}
        try:
            with open(self.registry_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("读取模型注册表失败: %s", e)
            return {"models": []}

    def route_challenge(
        self,
        instruction: str,
        challenge_type: Optional[str] = None,
        has_audio: bool = False,
    ) -> RouteDecision:
        """根据题干文本与上下文特征，智能决策最优打码路由"""
        clean_text = (instruction or "").lower().strip()
        models = self.registry.get("models", [])

        # 1. 尝试匹配专精 ONNX 小模型 (第一梯队)
        for m in models:
            keywords = m.get("trigger_keywords", [])
            for kw in keywords:
                if kw.lower() in clean_text:
                    weight_file = MODELS_DIR / m["filename"]
                    has_weight = weight_file.is_file()
                    
                    if has_weight:
                        return RouteDecision(
                            tier="ONNX_SPECIALIZED",
                            model_id=m["id"],
                            model_name=m["name"],
                            target_engine="onnxruntime",
                            confidence=0.96,
                            reason=f"题干关键词 '{kw}' 命中专精模型: {m['specialization']}",
                            weight_path=str(weight_file),
                        )
                    else:
                        # 专精模型命中但尚未下载权重，若支持本地 Ollama 则顺延至 Ollama，否则云端
                        logger.info("命中专精模型 %s，但本地权重暂未下载，自动平滑升阶...", m["name"])

        # 2. 检查是否有语音无障碍通道 (可选择听力降维)
        if has_audio or "audio" in clean_text:
            return RouteDecision(
                tier="WHISPER_AUDIO",
                model_id="whisper_base_int8",
                model_name="Whisper 本地听力打码模型",
                target_engine="faster-whisper",
                confidence=0.92,
                reason="检测到语音无障碍通道，启用本地 Whisper 0 成本降维打击",
                weight_path=None,
            )

        # 3. 检查本地 Ollama 视觉大模型配置 (第二梯队)
        ollama_base = os.environ.get("VISION_API_BASE", "")
        if "11434" in ollama_base or "localhost" in ollama_base:
            ollama_model = os.environ.get("VISION_MODEL", "qwen2.5-vl")
            return RouteDecision(
                tier="OLLAMA_LOCAL",
                model_id=ollama_model,
                model_name=f"本地 Ollama 端侧视觉模型 ({ollama_model})",
                target_engine="ollama",
                confidence=0.88,
                reason="未命中特定几何小模型，自动路由至本地 Ollama 通用端侧多模态模型",
                weight_path=None,
            )

        # 4. 回退至多模型云端投票 (第四梯队)
        return RouteDecision(
            tier="CLOUD_VOTE",
            model_id="multi_vision_voter",
            model_name="云端大模型仲裁投票池 (GPT-4V/Gemini/Claude)",
            target_engine="cloud_gateway",
            confidence=0.75,
            reason="题干需要通用常识理解，路由至云端多模型投票兜底",
            weight_path=None,
        )


# 全局单例
_router = CaptchaModelRouter()


def route_captcha(instruction: str, has_audio: bool = False) -> RouteDecision:
    """快速路由调用入口"""
    return _router.route_challenge(instruction, has_audio=has_audio)
