# -*- coding: utf-8 -*-
"""Batch B & Batch C Integration Test Suite

测试覆盖：
1. Batch B: PerimeterX / HUMAN 检测器与解密数据结构、生物按压微颤逻辑
2. Batch C: Agentic VLM 双阶段思维链 (CoT) 解析器、抗幻觉范围校验、Ollama 端侧路由
"""

import asyncio
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch

from core.perimeterx.detector import is_box_visible, PX_IFRAME_PATTERNS
from core.perimeterx.outlook_solver import PerimeterXSolution, _biometric_press_hold
from core.vision_solver.vlm_cot_solver import OllamaVLMSolver, CoTDecision
from core.vision_solver.model_router import CaptchaModelRouter, route_captcha
from core.human_dynamics import BiometricMouse


# =============================================================================
# Batch B 测试用例
# =============================================================================

def test_perimeterx_box_visibility():
    """测试 PerimeterX 视口可见性裁决"""
    viewport = (1280, 800)
    # 正常居中
    valid_box = {"x": 500, "y": 300, "width": 100, "height": 40}
    assert is_box_visible(valid_box, viewport) is True

    # 视口外部
    outside_box = {"x": 1500, "y": 300, "width": 100, "height": 40}
    assert is_box_visible(outside_box, viewport) is False

    # 尺寸过小不可视
    tiny_box = {"x": 100, "y": 100, "width": 10, "height": 5}
    assert is_box_visible(tiny_box, viewport) is False


def test_perimeterx_solution_contract():
    """测试 PerimeterX 凭证捆绑包契约及序列化"""
    sol = PerimeterXSolution(
        solved=True,
        px3="px3_mock_token_abc_123",
        cookies={"_px3": "px3_mock_token_abc_123", "_pxvid": "pxvid_uuid_999"},
        gate_reached=True,
        press_hold_actuated=True,
        px3_rotated=True,
        attempts=1,
        elapsed=5.2,
    )
    d = sol.to_dict()
    assert d["solved"] is True
    assert d["px3"] == "px3_mock_token_abc_123"
    assert "_px3" in d["cookies"]
    assert d["px3_rotated"] is True


def test_perimeterx_biometric_jitter():
    """测试 PerimeterX 生物微颤按压流程"""
    async def _run():
        mock_page = AsyncMock()
        mock_page.mouse = AsyncMock()
        
        # 模拟 is_perimeterx_visible 在循环第二次检测时提前返回 False (已通关)
        with patch("core.perimeterx.outlook_solver.is_perimeterx_visible", side_effect=[True, False]):
            target = {"x": 100, "y": 200, "width": 120, "height": 50}
            success = await _biometric_press_hold(mock_page, target, hold_min=1.0, hold_max=2.0)
            assert success is True
            # 确保按压生命周期完整 (down -> move tremor -> up)
            assert mock_page.mouse.down.called
            assert mock_page.mouse.up.called

    asyncio.run(_run())


# =============================================================================
# Batch C 测试用例
# =============================================================================

def test_vlm_cot_clean_json_parsing():
    """测试单次调用 CoT 的 Markdown 纯净 JSON 提取"""
    solver = OllamaVLMSolver()
    raw_markdown = """这里是思考过程：
经过两阶段空间感知，动物头部指向正左侧。在6个候选切片中，只有选项4的指针也是正左侧。
```json
{
  "reasoning": "目标头部向左，选项4手势完全指向左侧一致",
  "spatial_anchors": ["朝向: 左 (-90度)", "切片4: 指针完全对齐"],
  "answer": 4,
  "confidence": 0.98
}
```
已完成分析。"""

    decision = solver._parse_and_validate(raw_markdown, options_count=6, t0=0.0)
    assert decision.success is True
    assert decision.answer == 4
    assert decision.confidence == 0.98
    assert "选项4" in decision.reasoning
    assert len(decision.spatial_anchors) == 2


def test_vlm_cot_anti_hallucination_bounds_correction():
    """测试抗幻觉防御：候选越界修正与 0-based 映射"""
    solver = OllamaVLMSolver()
    
    # 1. 越界数字截断
    raw_overflow = json.dumps({"answer": 12, "confidence": 0.8})
    dec_overflow = solver._parse_and_validate(raw_overflow, options_count=6, t0=0.0)
    assert dec_overflow.answer == 6

    # 2. 0-based 映射为 1-based
    raw_zero = json.dumps({"answer": 0, "confidence": 0.85})
    dec_zero = solver._parse_and_validate(raw_zero, options_count=6, t0=0.0)
    assert dec_zero.answer == 1


def test_vlm_cot_offline_guard():
    """测试 Ollama 未启动时的优雅防崩守护"""
    solver = OllamaVLMSolver(base_url="http://127.0.0.1:59999")  # 无效端口
    dec = solver.solve("fake_b64", "选择正确朝向", options_count=6)
    assert dec.success is False
    assert "未启动" in (dec.error or "")


def test_model_router_tier_dispatch():
    """测试自适应模型路由分发器的阶梯决策"""
    router = CaptchaModelRouter()

    # 1. 命中已就绪的专精 ONNX 小模型 (第一梯队)
    dec_onnx = router.route_challenge("drag the slider to complete the puzzle")
    assert dec_onnx.tier == "ONNX_SPECIALIZED"
    assert dec_onnx.target_engine == "onnxruntime"
    assert "slider_gap_match.onnx" in (dec_onnx.weight_path or "")

    # 2. 命中声学降维 (第三梯队)
    dec_audio = router.route_challenge("press play to hear audio", has_audio=True)
    assert dec_audio.tier == "WHISPER_AUDIO"
    assert dec_audio.target_engine == "faster-whisper"

    # 3. 开放式多模态常识问题 (第四梯队)
    dec_general = router.route_challenge("which object is suitable for cutting bread?")
    assert dec_general.tier in ("OLLAMA_LOCAL", "CLOUD_VOTE")
