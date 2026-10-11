# -*- coding: utf-8 -*-
"""轻量级 SSO 测活器 (GrokSsoChecker) 存活/失效/标记解析与并发测活测试。"""
import pytest
from core.grok.sso_checker import (
    GrokSsoChecker,
    SsoCheckResult,
    batch_check_grok_liveness,
)


def test_sso_html_user_parsing():
    html = '''
    <!DOCTYPE html>
    <html>
      <script>
        window.__PRELOADED_STATE__ = {
          "initialData": {
            "user": {
              "id": "usr_998877",
              "email": "tester@example.com",
              "botFlagSource": 0,
              "botFlagDetails": "healthy_clean"
            }
          }
        };
      </script>
    </html>
    '''
    checker = GrokSsoChecker()
    user_info = checker._parse_user_data(html)
    assert user_info is not None
    assert user_info.get("id") == "usr_998877"

    src, det = checker._parse_bot_flags(user_info, html)
    assert src == 0
    assert det == "healthy_clean"


def test_sso_html_bot_flagged_parsing():
    html = '''
    <html>
      "initialData": {
        "user": {
          "id": "usr_bad",
          "botFlagSource": 2,
          "botFlagDetails": "ip_anomaly_risk"
        }
      }
    </html>
    '''
    checker = GrokSsoChecker()
    user_info = checker._parse_user_data(html)
    assert user_info is not None
    src, det = checker._parse_bot_flags(user_info, html)
    assert src == 2
    assert det == "ip_anomaly_risk"


def test_sso_check_account_expired(monkeypatch):
    checker = GrokSsoChecker()

    class ExpiredResp:
        status_code = 302
        url = "https://accounts.x.ai/sign-in?redirect=grok-com"
        text = "Redirecting to sign-in..."

    class DummySession:
        def get(self, *args, **kwargs):
            return ExpiredResp()

    from curl_cffi import requests as curl_requests
    monkeypatch.setattr(curl_requests, "Session", lambda *args, **kwargs: DummySession())

    acc = {"id": 1, "email": "test@expired.com", "sso_token": "expired_token"}
    res = checker.check_account(acc)
    assert res.ok is False
    assert res.status == "expired"
    assert "凭据已失效" in res.error


def test_batch_check_grok_liveness_mock(monkeypatch):
    def mock_check(self, account, explicit_proxy=None):
        acc_id = account["id"]
        status = "healthy" if acc_id % 2 == 1 else "expired"
        return SsoCheckResult(
            ok=(status == "healthy"),
            status=status,
            account_id=acc_id,
            email=account["email"],
            user_id=f"usr_{acc_id}",
        )

    monkeypatch.setattr(GrokSsoChecker, "check_account", mock_check)

    accounts = [
        {"id": 1, "email": "u1@test.com", "sso_token": "tok1"},
        {"id": 2, "email": "u2@test.com", "sso_token": "tok2"},
        {"id": 3, "email": "u3@test.com", "sso_token": "tok3"},
    ]
    results = batch_check_grok_liveness(accounts, concurrency=2)
    assert len(results) == 3
    statuses = {r.account_id: r.status for r in results}
    assert statuses[1] == "healthy"
    assert statuses[2] == "expired"
    assert statuses[3] == "healthy"
