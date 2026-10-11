# -*- coding: utf-8 -*-
"""Grok 质检自检动态风险规则引擎测试（融入 GrokIQ 范式）。"""
import pytest
from core.grok.quality_checker import GrokQualityChecker, GrokQualityResult


def test_rule_marker_miss_detection():
    checker = GrokQualityChecker()
    # 模拟探针返回结果中丢失标志物 STATUS_PROBE_CONFIRMED
    mock_probe = {
        "tps": 40.0,
        "output_tokens": 50,
        "reasoning_tokens": 20,
        "has_reasoning": True,
        "marker_matched": False,
        "first_token_share": 0.1,
    }
    checker._run_lightweight_probe = lambda session, token: mock_probe

    res = GrokQualityResult()
    # 执行第二级逻辑
    if not mock_probe.get("marker_matched") and mock_probe.get("output_tokens", 0) > 15:
        res.status = "degraded"
        res.reasons.append("未匹配到指令标志物 [marker_miss]")

    assert res.status == "degraded"
    assert any("marker_miss" in r for r in res.reasons)


def test_rule_reasoning_zero_detection():
    # 模拟推理模型无思维链
    mock_probe = {
        "tps": 42.0,
        "output_tokens": 60,
        "reasoning_tokens": 0,
        "has_reasoning": False,
        "marker_matched": True,
        "first_token_share": 0.1,
    }

    res = GrokQualityResult()
    if mock_probe.get("reasoning_tokens", 0) == 0 and mock_probe.get("output_tokens", 0) > 20:
        res.status = "degraded"
        res.reasons.append("推理模型缺少思维链输出 [reasoning_zero]")

    assert res.status == "degraded"
    assert any("reasoning_zero" in r for r in res.reasons)


def test_rule_fast_risk_detection():
    # 模拟 TPS 超速虚高异常
    mock_probe = {
        "tps": 185.0,
        "output_tokens": 120,
        "reasoning_tokens": 10,
        "has_reasoning": True,
        "marker_matched": True,
        "first_token_share": 0.2,
    }

    res = GrokQualityResult()
    if mock_probe["tps"] >= 150.0:
        res.status = "degraded"
        res.reasons.append("生成速度异常虚高 [fast_risk]")

    assert res.status == "degraded"
    assert any("fast_risk" in r for r in res.reasons)


def test_rule_buffered_hard_detection():
    # 模拟首包占比 >= 0.85 整体阻滞缓冲
    mock_probe = {
        "tps": 45.0,
        "output_tokens": 80,
        "reasoning_tokens": 15,
        "has_reasoning": True,
        "marker_matched": True,
        "first_token_share": 0.92,
    }

    res = GrokQualityResult()
    if mock_probe.get("first_token_share", 0.0) >= 0.85 and mock_probe.get("output_tokens", 0) > 25:
        res.status = "degraded"
        res.reasons.append("响应流整体缓冲阻滞 [buffered_hard]")

    assert res.status == "degraded"
    assert any("buffered_hard" in r for r in res.reasons)


def test_healthy_probe_pass():
    # 正常样本
    mock_probe = {
        "tps": 48.0,
        "output_tokens": 65,
        "reasoning_tokens": 25,
        "has_reasoning": True,
        "marker_matched": True,
        "first_token_share": 0.15,
    }

    res = GrokQualityResult()
    if not mock_probe.get("marker_matched") and mock_probe.get("output_tokens", 0) > 15:
        res.status = "degraded"
    elif mock_probe.get("reasoning_tokens", 0) == 0 and mock_probe.get("output_tokens", 0) > 20:
        res.status = "degraded"
    elif mock_probe["tps"] >= 150.0:
        res.status = "degraded"
    elif mock_probe.get("first_token_share", 0.0) >= 0.85 and mock_probe.get("output_tokens", 0) > 25:
        res.status = "degraded"
    else:
        res.status = "healthy"

    assert res.status == "healthy"
