# -*- coding: utf-8 -*-
"""GrokIQ 探针画像池轮换、输出不足与软异常判定测试。"""
import pytest
from core.grok.quality_checker import (
    GrokQualityChecker,
    DEFAULT_PROBE_PROFILES,
    GrokQualityResult,
)


def test_probe_profiles_structure():
    assert len(DEFAULT_PROBE_PROFILES) >= 3
    profile_ids = [p["id"] for p in DEFAULT_PROBE_PROFILES]
    assert "quality-marker" in profile_ids
    assert "reasoning-check" in profile_ids
    assert "html-preview" in profile_ids

    for p in DEFAULT_PROBE_PROFILES:
        assert p.get("prompt")
        assert p.get("marker")
        assert p.get("model")


def test_rule_insufficient_output_detection(monkeypatch):
    checker = GrokQualityChecker()
    monkeypatch.setattr(checker, "_extract_initial_user", lambda html: {"id": "usr_123"})
    monkeypatch.setattr(checker, "_extract_bot_flags", lambda u, h: (None, ""))

    # 模拟探针返回极短内容 (output_tokens = 2 < 5)
    def mock_short_probe(session, token, profile_id=None):
        return {
            "tps": 10.0,
            "output_tokens": 2,
            "reasoning_tokens": 10,
            "has_reasoning": True,
            "first_token_ms": 300,
            "first_token_share": 0.5,
            "marker_matched": True,
            "profile_id": profile_id or "quality-marker",
            "output_text": "Hi",
        }

    monkeypatch.setattr(checker, "_run_lightweight_probe", mock_short_probe)

    class DummyResp:
        status_code = 200
        text = '<html>"initialData":{"user":{"id":"usr_123"}}</html>'
        url = "https://grok.com/"

    class DummySession:
        def get(self, *args, **kwargs):
            return DummyResp()

    from curl_cffi import requests as curl_requests
    monkeypatch.setattr(curl_requests, "Session", lambda *args, **kwargs: DummySession())

    res = checker.check(sso_token="dummy_token", run_probe=True)
    assert res.status == "degraded"
    assert any("insufficient_output" in r for r in res.reasons)


def test_rule_buffered_soft_observation(monkeypatch):
    checker = GrokQualityChecker()
    monkeypatch.setattr(checker, "_extract_initial_user", lambda html: {"id": "usr_123"})
    monkeypatch.setattr(checker, "_extract_bot_flags", lambda u, h: (None, ""))

    # 模拟轻度缓冲但未达到 150 强异常 (TPS=95, first_token_share=0.75)
    def mock_soft_probe(session, token, profile_id=None):
        return {
            "tps": 95.0,
            "output_tokens": 50,
            "reasoning_tokens": 30,
            "has_reasoning": True,
            "first_token_ms": 250,
            "first_token_share": 0.75,
            "marker_matched": True,
            "profile_id": "reasoning-check",
            "output_text": "MARKER_OK RESULT=53",
        }

    monkeypatch.setattr(checker, "_run_lightweight_probe", mock_soft_probe)

    class DummyResp:
        status_code = 200
        text = '<html>"initialData":{"user":{"id":"usr_123"}}</html>'
        url = "https://grok.com/"

    class DummySession:
        def get(self, *args, **kwargs):
            return DummyResp()

    from curl_cffi import requests as curl_requests
    monkeypatch.setattr(curl_requests, "Session", lambda *args, **kwargs: DummySession())

    res = checker.check(sso_token="dummy_token", run_probe=True, profile_id="reasoning-check")
    assert res.status == "healthy"
    assert any("buffered_soft" in r for r in res.reasons)
    assert res.probe_profile_used == "reasoning-check"
