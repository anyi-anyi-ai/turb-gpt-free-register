# -*- coding: utf-8 -*-
"""
Stable Audio (stableaudio.com) 本地持久化与网关解耦同步模块
负责：
  - 初始化 turb.sqlite3 中的 stableaudio_accounts 数据表
  - 本地落盘 Auth0 Token、Refresh Token、Cookies 与账号额度
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

logger = logging.getLogger("stableaudio.sync")

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


def init_stableaudio_db(db_path: Path | str | None = None) -> None:
    """初始化 SQLite 中的 stableaudio_accounts 凭证管理表。"""
    with _get_sync_conn(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS stableaudio_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password TEXT NOT NULL,
                access_token TEXT NOT NULL,
                refresh_token TEXT,
                token_type TEXT DEFAULT 'Bearer',
                credits INTEGER DEFAULT 50,
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
            CREATE INDEX IF NOT EXISTS idx_sa_email ON stableaudio_accounts(email);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_sa_status ON stableaudio_accounts(status);
        """)
        conn.commit()


def save_stableaudio_account_to_db(
    email: str,
    password: str,
    access_token: str = "",
    refresh_token: Optional[str] = None,
    token_type: str = "Bearer",
    credits: int = 50,
    cookies: Optional[Dict[str, str]] = None,
    proxy_used: Optional[str] = None,
    status: str = "active",
    payload: Optional[Dict[str, Any]] = None,
    db_path: Path | str | None = None,
) -> bool:
    """持久化 Stable Audio 账号凭据至本地数据库。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    init_stableaudio_db(p)

    now = datetime.now(timezone.utc).isoformat()
    cookies_json = json.dumps(cookies or {}, ensure_ascii=False)
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    try:
        with _get_sync_conn(db_path) as conn:
            conn.execute("""
                INSERT INTO stableaudio_accounts (
                    email, password, access_token, refresh_token, token_type,
                    credits, cookies_json, proxy_used, status, synced_to_gateway,
                    created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    password = excluded.password,
                    access_token = excluded.access_token,
                    refresh_token = COALESCE(excluded.refresh_token, stableaudio_accounts.refresh_token),
                    token_type = excluded.token_type,
                    credits = excluded.credits,
                    cookies_json = excluded.cookies_json,
                    proxy_used = excluded.proxy_used,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json;
            """, (
                email.lower().strip(),
                password,
                access_token,
                refresh_token or "",
                token_type,
                credits,
                cookies_json,
                proxy_used or "",
                status,
                now,
                now,
                payload_json
            ))
            conn.commit()
            logger.info(f"[DB] Stable Audio 账号持久化成功: {email}")
            return True
    except Exception as e:
        logger.error(f"[DB] Stable Audio 账号入库失败 ({email}): {e}")
        return False


def push_account_to_music2api(
    email: str,
    access_token: str,
    refresh_token: str = "",
    cookies: Optional[Dict[str, str]] = None,
    credits: int = 50,
    label: Optional[str] = None,
    gateway_url: str = GATEWAY_BASE_URL,
    admin_token: str = GATEWAY_ADMIN_TOKEN,
    db_path: Path | str | None = None,
) -> bool:
    """通过 Webhook 将凭证推送到下游聚合网关 music2api。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    endpoint = f"{gateway_url.rstrip('/')}/admin/accounts"

    body_data = {
        "provider": "stableaudio",
        "email": email.lower().strip(),
        "label": label or f"stableaudio_{email.split('@')[0]}",
        "cookies": cookies or {},
        "credentials": {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "Bearer",
            "credits": credits
        },
        "session_jwt": access_token,
        "clerk_client_id": refresh_token,
        "credits": credits
    }

    req = urllib.request.Request(
        endpoint,
        data=json.dumps(body_data).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {admin_token}",
            "User-Agent": "Turb-Register-Engine/1.0"
        }
    )

    try:
        with urllib.request.urlopen(req, timeout=8) as res:
            if res.status in (200, 201):
                logger.info(f"[Gateway] 账号推送到网关成功: {email} -> {endpoint}")
                with _get_sync_conn(db_path) as conn:
                    conn.execute("UPDATE stableaudio_accounts SET synced_to_gateway = 1 WHERE LOWER(email) = ?", (email.lower(),))
                    conn.commit()
                return True
            else:
                logger.warning(f"[Gateway] 网关响应异常 ({res.status}): {res.read().decode()[:200]}")
                return False
    except Exception as e:
        logger.warning(f"[Gateway] 推送网关暂时不可达 (可稍后离线补推): {e}")
        return False
