# -*- coding: utf-8 -*-
"""Grok 账号向 gptGrok2api 接口推送与分组映射测试。"""
import pytest
from core.gptgrok2api_sync import (
    map_country_to_group_id,
    push_grok_accounts_to_gptgrok2api,
    GROUP_ID_ASIA,
    GROUP_ID_EUROPE,
    GROUP_ID_AMERICAS,
)


def test_group_mapping_for_grok():
    assert map_country_to_group_id("JP") == GROUP_ID_ASIA
    assert map_country_to_group_id("SG") == GROUP_ID_ASIA
    assert map_country_to_group_id("GB") == GROUP_ID_EUROPE
    assert map_country_to_group_id("DE") == GROUP_ID_EUROPE
    assert map_country_to_group_id("US") == GROUP_ID_AMERICAS
    assert map_country_to_group_id("CA") == GROUP_ID_AMERICAS


def test_push_grok_accounts_payload_formatting(monkeypatch):
    captured_payloads = []

    def mock_post(url, headers=None, json=None, timeout=None):
        nonlocal captured_payloads
        captured_payloads = json.get("accounts", [])

        class MockResp:
            status_code = 200

            def json(self):
                return {"added": len(captured_payloads), "skipped": 0}

        return MockResp()

    import requests
    monkeypatch.setattr(requests, "post", mock_post)

    grok_accs = [
        {
            "id": 101,
            "email": "grok_asia@example.com",
            "password": "Password123!",
            "sso_token": "sso_tok_asia",
            "country": "JP",
            "status": "healthy",
        },
        {
            "id": 102,
            "email": "grok_us@example.com",
            "password": "Password456!",
            "sso_token": "sso_tok_us",
            "country": "US",
            "status": "degraded",
        },
    ]

    res = push_grok_accounts_to_gptgrok2api(grok_accs)
    assert res["ok"] is True
    assert len(captured_payloads) == 2

    asia_acc = next(a for a in captured_payloads if a["email"] == "grok_asia@example.com")
    assert asia_acc["access_token"] == "sso_tok_asia"
    assert asia_acc["group_id"] == GROUP_ID_ASIA
    assert asia_acc["source_type"] == "grok"
    assert asia_acc["enabled"] is True

    us_acc = next(a for a in captured_payloads if a["email"] == "grok_us@example.com")
    assert us_acc["access_token"] == "sso_tok_us"
    assert us_acc["group_id"] == GROUP_ID_AMERICAS
    assert us_acc["enabled"] is False  # degraded accounts are disabled by default
