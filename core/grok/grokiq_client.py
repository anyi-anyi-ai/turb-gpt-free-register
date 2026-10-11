# -*- coding: utf-8 -*-
"""GrokIQ Webhook 联动与 Outbox 持久化投递客户端。

实现注册机向 GrokIQ 质检中台的持久化异步投递机制（At-least-once 语义），
具备幂等去重、指数退避重试与两阶段状态同步支持。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from core import db

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

logger = logging.getLogger(__name__)

RETRY_DELAYS = (2, 5, 10, 30, 60, 120, 300)


def get_grokiq_config() -> dict[str, Any]:
    enabled = os.environ.get("GROKIQ_WEBHOOK_ENABLED", "false").lower() in ("true", "1", "yes")
    url = os.environ.get("GROKIQ_WEBHOOK_URL", "http://127.0.0.1:8090/api/v1/register/webhook").strip()
    token = os.environ.get("GROKIQ_WEBHOOK_TOKEN", "").strip()
    try:
        timeout = int(os.environ.get("GROKIQ_WEBHOOK_TIMEOUT", "10"))
    except Exception:
        timeout = 10
    return {
        "enabled": enabled,
        "url": url,
        "token": token,
        "timeout": max(2, min(timeout, 60)),
    }


def enqueue_grok_event(
    *,
    account_id: int,
    email: str,
    sso: str = "",
    status: str = "healthy",
    bfs: int | None = None,
    tps: float = 0.0,
) -> str | None:
    """将注册成功的 Grok 账号打包为事件入队持久化 Outbox。"""
    cfg = get_grokiq_config()
    if not cfg["enabled"]:
        return None

    event_id = f"evt_{uuid.uuid4().hex[:16]}"
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "event_id": event_id,
        "occurred_at": now_iso,
        "account": {
            "email": email,
            "sso": sso,
            "status": status,
            "bot_risk": status in ("bot_flagged", "degraded"),
            "bfs": bfs,
            "tps": tps,
            "source": "turb-grok-register",
        },
    }

    db.enqueue_grokiq_outbox(
        event_id=event_id,
        account_id=account_id,
        email=email,
        payload=payload,
    )
    grokiq_notifier.wake()
    return event_id


class GrokIQNotifier:
    """Outbox 队列后台消费守护线程。"""

    def __init__(self):
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._wake_event = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._thread = threading.Thread(target=self._run_loop, daemon=True, name="grokiq-outbox-worker")
            self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        self._wake_event.set()

    def wake(self) -> None:
        self._wake_event.set()

    def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            cfg = get_grokiq_config()
            if not cfg["enabled"] or not cfg["url"]:
                # 未启用时休眠等待唤醒
                self._wake_event.wait(timeout=10.0)
                self._wake_event.clear()
                continue

            events = db.fetch_pending_outbox_events(limit=15)
            if not events:
                self._wake_event.wait(timeout=5.0)
                self._wake_event.clear()
                continue

            for ev in events:
                if self._stop_event.is_set():
                    break
                self._deliver_event(ev, cfg)

    def _deliver_event(self, ev: dict[str, Any], cfg: dict[str, Any]) -> None:
        event_id = ev["event_id"]
        payload = ev.get("payload") or {}
        retry_count = int(ev.get("retry_count") or 0)
        account_id = int(ev.get("account_id") or 0)

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "TURB-Grok-Register/1.0",
        }
        if cfg["token"]:
            headers["Authorization"] = f"Bearer {cfg['token']}"
            headers["X-Webhook-Token"] = cfg["token"]

        session = None
        if curl_requests:
            session = curl_requests.Session(impersonate="chrome124")
        else:
            import requests
            session = requests.Session()

        try:
            resp = session.post(
                cfg["url"],
                json=payload,
                headers=headers,
                timeout=cfg["timeout"],
            )
            # 200 OK 或 409 Conflict (已存在/去重)
            if resp.status_code in (200, 201, 204, 409):
                db.update_outbox_status(
                    event_id,
                    status="sent",
                    retry_count=retry_count,
                    next_retry_at=0.0,
                    last_error="",
                )
                if account_id > 0:
                    db.mark_account_grokiq_synced(account_id)
                logger.info("[GrokIQ] 事件 %s 投递成功 (HTTP %d)", event_id, resp.status_code)
                return

            error_msg = f"HTTP {resp.status_code}: {resp.text[:100]}"
        except Exception as exc:
            error_msg = str(exc)
        finally:
            try:
                session.close()
            except Exception:
                pass

        # 投递失败，计算下一次重试时间
        delay_index = min(retry_count, len(RETRY_DELAYS) - 1)
        delay_sec = RETRY_DELAYS[delay_index]
        new_status = "retrying" if retry_count < len(RETRY_DELAYS) * 2 else "failed"
        next_ts = time.time() + delay_sec

        db.update_outbox_status(
            event_id,
            status=new_status,
            retry_count=retry_count + 1,
            next_retry_at=next_ts,
            last_error=error_msg,
        )
        logger.warning("[GrokIQ] 事件 %s 投递失败 (%s)，将于 %ds 后重试", event_id, error_msg, delay_sec)


# 全局单例并自动启动后台消费
grokiq_notifier = GrokIQNotifier()
grokiq_notifier.start()
