# -*- coding: utf-8 -*-
"""GrokIQ Outbox 持久队列与 Webhook 投递测试。"""
import time
import pytest
from core import db
from core.grok.grokiq_client import enqueue_grok_event, get_grokiq_config


def test_grokiq_outbox_crud():
    test_event_id = f"test_evt_{int(time.time() * 1000)}"
    test_email = "outbox_test@example.com"
    payload = {
        "event_id": test_event_id,
        "account": {"email": test_email, "status": "healthy"},
    }

    # 入队
    row_id = db.enqueue_grokiq_outbox(
        event_id=test_event_id,
        account_id=999,
        email=test_email,
        payload=payload,
    )
    assert row_id > 0

    # 获取待投递列表
    events = db.fetch_pending_outbox_events(limit=50)
    target = next((e for e in events if e["event_id"] == test_event_id), None)
    assert target is not None
    assert target["email"] == test_email
    assert target["status"] == "pending"

    # 更新为重试中
    next_retry = time.time() + 30
    db.update_outbox_status(
        test_event_id,
        status="retrying",
        retry_count=1,
        next_retry_at=next_retry,
        last_error="HTTP 502 Bad Gateway",
    )

    # 再次查询（由于 next_retry_at > now，默认 fetch 应该排除）
    events_after = db.fetch_pending_outbox_events(limit=50)
    assert not any(e["event_id"] == test_event_id for e in events_after)

    # 标记已成功投递
    db.update_outbox_status(
        test_event_id,
        status="sent",
        retry_count=1,
        next_retry_at=0.0,
        last_error="",
    )


def test_grokiq_config_loading(monkeypatch):
    monkeypatch.setenv("GROKIQ_WEBHOOK_ENABLED", "True")
    monkeypatch.setenv("GROKIQ_WEBHOOK_URL", "https://grokiq.test.local/api/v1/webhook")
    monkeypatch.setenv("GROKIQ_WEBHOOK_TOKEN", "token_123456")

    cfg = get_grokiq_config()
    assert cfg["enabled"] is True
    assert cfg["url"] == "https://grokiq.test.local/api/v1/webhook"
    assert cfg["token"] == "token_123456"
