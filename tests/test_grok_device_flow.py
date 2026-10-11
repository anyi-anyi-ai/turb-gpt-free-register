# -*- coding: utf-8 -*-
"""Grok OAuth Device Code 授权与 CPA 凭据转换测试。"""
import json
import pytest
from core import db
from core.grok.auth_exchange import (
    decode_jwt_payload,
    token_to_cpa_record,
    token_to_grok2api_account,
    CLIENT_ID,
    OIDC_ISSUER,
)


def test_jwt_payload_decode():
    # header.payload.signature
    # {"sub": "usr_987654", "email": "test_jwt@example.com", "exp": 1799999999}
    token = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAidXNyXzk4NzY1NCIsICJlbWFpbCI6ICJ0ZXN0X2p3dEBleGFtcGxlLmNvbSIsICJleHAiOiAxNzk5OTk5OTk5fQ.signature"
    claims = decode_jwt_payload(token)
    assert claims.get("sub") == "usr_987654"
    assert claims.get("email") == "test_jwt@example.com"
    assert claims.get("exp") == 1799999999


def test_token_to_cpa_record_format():
    token_payload = {
        "access_token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiAidXNyXzEyMyIsICJlbWFpbCI6ICJjcGFfdGVzdEBleGFtcGxlLmNvbSIsICJleHAiOiAxNzg0MDAwMDAwfQ.sig",
        "refresh_token": "mock_refresh_token_xyz",
        "id_token": "",
        "token_type": "Bearer",
        "expires_in": 3600,
    }
    cpa = token_to_cpa_record(token_payload, email="cpa_test@example.com", sso="mock_sso_cookie")

    assert cpa["type"] == "xai"
    assert cpa["auth_kind"] == "oauth"
    assert cpa["email"] == "cpa_test@example.com"
    assert cpa["sub"] == "usr_123"
    assert cpa["access_token"] == token_payload["access_token"]
    assert cpa["refresh_token"] == "mock_refresh_token_xyz"
    assert cpa["base_url"] == "https://cli-chat-proxy.grok.com/v1"
    assert cpa["sso"] == "mock_sso_cookie"
    assert "headers" in cpa
    assert cpa["headers"]["X-XAI-Token-Auth"] == "xai-grok-cli"


def test_cpa_export_from_db():
    email = "cpa_db_export@example.com"
    cpa_json = json.dumps({
        "type": "xai",
        "email": email,
        "access_token": "at_12345",
        "refresh_token": "rt_12345",
    })
    db.insert_grok_account(
        email=email,
        password="Pwd123456!",
        sso_token="sso_12345",
        refresh_token="rt_12345",
        access_token="at_12345",
        cpa_record=cpa_json,
        status="healthy",
    )

    cpa_export_str = db.export_grok_accounts_text(format_type="cpa")
    records = json.loads(cpa_export_str)
    assert any(r.get("email") == email for r in records)

    # 清理
    db.delete_grok_accounts(emails=[email])
