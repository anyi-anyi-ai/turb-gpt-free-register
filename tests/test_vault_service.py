# -*- coding: utf-8 -*-
"""测试 Universal Vault Service 跨平台账号资产管理。"""
import json
import pytest
from core import vault_service

def test_list_accounts_all():
    """测试跨平台统一账号列表拉取。"""
    res = vault_service.list_accounts(page=1, page_size=20)
    assert res["ok"] is True
    assert "items" in res
    assert "total" in res
    assert res["total"] >= 0
    if res["items"]:
        first = res["items"][0]
        assert "id" in first
        assert "platform" in first
        assert "account_id" in first
        assert "status" in first
        assert "has_token" in first
        assert "has_password" in first
        # 验证默认脱敏，严禁包含明文 token/password 字段
        assert "password" not in first
        assert "access_token" not in first
        assert "totp_secret" not in first

def test_list_accounts_by_platform():
    """测试按特定平台过滤。"""
    for p in ["chatgpt", "grok", "github", "elevenlabs"]:
        res = vault_service.list_accounts(platform=p, page=1, page_size=10)
        assert res["ok"] is True
        for it in res["items"]:
            assert it["platform"] == p

def test_get_vault_metrics():
    """测试资产大盘指标统计。"""
    metrics = vault_service.get_vault_metrics()
    assert metrics["ok"] is True
    assert "total_accounts" in metrics
    assert "platforms" in metrics
    assert "chatgpt" in metrics["platforms"]
    assert "grok" in metrics["platforms"]
    assert metrics["platforms"]["chatgpt"]["total"] >= 0

def test_get_account_secret():
    """测试按需提取机密凭据。"""
    # 先取一个账号
    res = vault_service.list_accounts(platform="chatgpt", page=1, page_size=1)
    if res["items"]:
        acc = res["items"][0]
        sec_res = vault_service.get_account_secret("chatgpt", acc["id"])
        assert sec_res["ok"] is True
        assert "secrets" in sec_res
        assert "password" in sec_res["secrets"]
        assert "access_token" in sec_res["secrets"]

def test_export_accounts_formats():
    """测试多格式导出 (Sub2API, OpenAI, CSV, TXT)。"""
    for fmt in ["sub2api", "openai", "csv", "txt"]:
        exp = vault_service.export_accounts(platform="chatgpt", format_type=fmt)
        assert exp["ok"] is True
        assert "content" in exp
        assert "filename" in exp
        assert len(exp["content"]) >= 0
