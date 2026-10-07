# -*- coding: utf-8 -*-
"""
ElevenLabs (elevenlabs.io) 本地持久化与网关解耦同步模块
负责：
  - 初始化 turb.sqlite3 中的 elevenlabs_accounts 数据表
  - 本地落盘 API Key (xi-api-key)、Session Token、Cookies 与每月字符额度 (10,000)
  - 异步/同步将凭据推送至下游聚合网关 music2api (/admin/accounts)
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
import urllib.request
import urllib.error

logger = logging.getLogger("elevenlabs.sync")

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _ROOT / "turb.sqlite3"
GATEWAY_BASE_URL = "http://127.0.0.1:18620"
GATEWAY_ADMIN_TOKEN = "music2api-admin-secret-key-change-me"


def _get_sync_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(db_path or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


def init_elevenlabs_db(db_path: Path | str | None = None) -> None:
    """初始化 SQLite 中的 elevenlabs_accounts 凭证管理表。"""
    with _get_sync_conn(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS elevenlabs_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                api_key TEXT,
                session_token TEXT,
                credits INTEGER DEFAULT 10000,
                cookies_json TEXT NOT NULL,
                proxy_used TEXT,
                status TEXT DEFAULT 'active',
                synced_to_gateway INTEGER DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                payload_json TEXT
            );
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_el_email ON elevenlabs_accounts(email);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_el_status ON elevenlabs_accounts(status);
        """)
        conn.commit()


def save_elevenlabs_account_to_db(
    email: str,
    password: str,
    api_key: str = "",
    session_token: str = "",
    credits: int = 10000,
    cookies: Optional[Dict[str, str]] = None,
    proxy_used: Optional[str] = None,
    status: str = "active",
    payload: Optional[Dict[str, Any]] = None,
    db_path: Path | str | None = None,
) -> bool:
    """持久化 ElevenLabs 账号凭据至本地数据库。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    init_elevenlabs_db(p)

    now = datetime.now(timezone.utc).isoformat()
    cookies_json = json.dumps(cookies or {}, ensure_ascii=False)
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    try:
        with _get_sync_conn(db_path) as conn:
            conn.execute("""
                INSERT INTO elevenlabs_accounts (
                    email, password, api_key, session_token,
                    credits, cookies_json, proxy_used, status, synced_to_gateway,
                    created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    password = excluded.password,
                    api_key = COALESCE(excluded.api_key, elevenlabs_accounts.api_key),
                    session_token = excluded.session_token,
                    credits = excluded.credits,
                    cookies_json = excluded.cookies_json,
                    proxy_used = excluded.proxy_used,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json;
            """, (
                email.lower().strip(),
                password,
                api_key,
                session_token,
                credits,
                cookies_json,
                proxy_used or "",
                status,
                now,
                now,
                payload_json
            ))
            conn.commit()
            logger.info(f"[DB] ElevenLabs 账号持久化成功: {email} (status={status})")
            return True
    except Exception as e:
        logger.error(f"[DB] 保存 ElevenLabs 账号失败: {e}")
        return False


def push_account_to_music2api(
    email: str,
    api_key: str = "",
    session_token: str = "",
    cookies: Optional[Dict[str, str]] = None,
    credits: int = 10000,
    gateway_url: str = GATEWAY_BASE_URL,
    admin_token: str = GATEWAY_ADMIN_TOKEN,
    db_path: Path | str | None = None,
) -> bool:
    """向下游聚合网关 music2api 推送新激活的 ElevenLabs 凭证。"""
    endpoint = f"{gateway_url.rstrip('/')}/admin/accounts"
    payload = {
        "provider": "elevenlabs",
        "email": email.lower().strip(),
        "label": f"elevenlabs_{email.split('@')[0]}",
        "clerk_client_id": api_key or "",
        "session_jwt": session_token or api_key or "",
        "cookies": cookies or {},
        "credits": credits,
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {admin_token}",
            "User-Agent": "turb-register-machine/1.0",
        },
        method="POST"
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status in (200, 201):
                logger.info(f"[Gateway] ElevenLabs 账号推送到网关成功: {email} -> {endpoint}")
                _mark_account_synced(email, db_path=db_path)
                return True
            else:
                logger.warning(f"[Gateway] 网关返回非 200 响应: {resp.status}")
                return False
    except Exception as e:
        logger.warning(f"[Gateway] 推送 ElevenLabs 账号到网关失败: {e} (已在本地安全落盘，支持离线回补)")
        return False


def _mark_account_synced(email: str, db_path: Path | str | None = None) -> None:
    try:
        with _get_sync_conn(db_path) as conn:
            conn.execute("""
                UPDATE elevenlabs_accounts
                SET synced_to_gateway = 1,
                    updated_at = datetime('now')
                WHERE LOWER(email) = ?;
            """, (email.lower().strip(),))
            conn.commit()
    except Exception as e:
        logger.error(f"[DB] 更新 ElevenLabs 同步标记失败 ({email}): {e}")


def sync_all_unpushed_elevenlabs_accounts(
    gateway_url: str = GATEWAY_BASE_URL,
    admin_token: str = GATEWAY_ADMIN_TOKEN,
    db_path: Path | str | None = None,
) -> Tuple[int, int]:
    """批量同步所有未推送的活跃 ElevenLabs 账号至聚合网关。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    init_elevenlabs_db(p)

    with _get_sync_conn(p) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT email, api_key, session_token, credits, cookies_json
            FROM elevenlabs_accounts
            WHERE synced_to_gateway = 0 AND status = 'active';
        """).fetchall()

    total = len(rows)
    success = 0
    for r in rows:
        cookies = json.loads(r["cookies_json"]) if r["cookies_json"] else {}
        ok = push_account_to_music2api(
            email=r["email"],
            api_key=r["api_key"] or "",
            session_token=r["session_token"] or "",
            cookies=cookies,
            credits=r["credits"] or 10000,
            gateway_url=gateway_url,
            admin_token=admin_token,
            db_path=p,
        )
        if ok:
            success += 1

    return success, total
