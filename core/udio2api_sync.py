# -*- coding: utf-8 -*-
"""
Udio (udio.com) 本地持久化与网关解耦同步模块
负责：
  - 初始化 turb.sqlite3 中的 udio_accounts 数据表
  - 本地落盘 Supabase Auth JWT、Refresh Token、Cookies 与账号额度
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

logger = logging.getLogger("udio.sync")

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


def init_udio_db(db_path: Path | str | None = None) -> None:
    """初始化 SQLite 中的 udio_accounts 凭证管理表。"""
    with _get_sync_conn(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS udio_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                username TEXT,
                access_token TEXT,
                refresh_token TEXT,
                token_type TEXT DEFAULT 'Bearer',
                credits INTEGER DEFAULT 10,
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
            CREATE INDEX IF NOT EXISTS idx_udio_email ON udio_accounts(email);
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_udio_status ON udio_accounts(status);
        """)
        conn.commit()


def save_udio_account_to_db(
    email: str,
    username: str = "",
    access_token: str = "",
    refresh_token: Optional[str] = None,
    token_type: str = "Bearer",
    credits: int = 10,
    cookies: Optional[Dict[str, str]] = None,
    proxy_used: Optional[str] = None,
    status: str = "active",
    payload: Optional[Dict[str, Any]] = None,
    db_path: Path | str | None = None,
) -> bool:
    """持久化 Udio 账号凭据至本地数据库。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    init_udio_db(p)

    now = datetime.now(timezone.utc).isoformat()
    cookies_json = json.dumps(cookies or {}, ensure_ascii=False)
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    try:
        with _get_sync_conn(db_path) as conn:
            conn.execute("""
                INSERT INTO udio_accounts (
                    email, username, access_token, refresh_token, token_type,
                    credits, cookies_json, proxy_used, status, synced_to_gateway,
                    created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    username = COALESCE(excluded.username, udio_accounts.username),
                    access_token = excluded.access_token,
                    refresh_token = COALESCE(excluded.refresh_token, udio_accounts.refresh_token),
                    token_type = excluded.token_type,
                    credits = excluded.credits,
                    cookies_json = excluded.cookies_json,
                    proxy_used = excluded.proxy_used,
                    status = excluded.status,
                    updated_at = excluded.updated_at,
                    payload_json = excluded.payload_json;
            """, (
                email.lower().strip(),
                username,
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
            logger.info(f"[DB] Udio 账号持久化成功: {email} (status={status})")
            return True
    except Exception as e:
        logger.error(f"[DB] 保存 Udio 账号失败: {e}")
        return False


def push_account_to_music2api(
    email: str,
    access_token: str = "",
    refresh_token: Optional[str] = None,
    cookies: Optional[Dict[str, str]] = None,
    credits: int = 10,
    gateway_url: str = GATEWAY_BASE_URL,
    admin_token: str = GATEWAY_ADMIN_TOKEN,
    db_path: Path | str | None = None,
) -> bool:
    """向下游聚合网关 music2api 推送新激活的 Udio 凭证。"""
    endpoint = f"{gateway_url.rstrip('/')}/admin/accounts"
    payload = {
        "provider": "udio",
        "email": email.lower().strip(),
        "label": f"udio_{email.split('@')[0]}",
        "clerk_client_id": refresh_token or "",
        "session_jwt": access_token or "",
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
                logger.info(f"[Gateway] Udio 账号推送到网关成功: {email} -> {endpoint}")
                _mark_account_synced(email, db_path=db_path)
                return True
            else:
                logger.warning(f"[Gateway] 网关返回非 200 响应: {resp.status}")
                return False
    except Exception as e:
        logger.warning(f"[Gateway] 推送 Udio 账号到网关失败: {e} (已在本地安全落盘，支持离线回补)")
        return False


def _mark_account_synced(email: str, db_path: Path | str | None = None) -> None:
    try:
        with _get_sync_conn(db_path) as conn:
            conn.execute("""
                UPDATE udio_accounts
                SET synced_to_gateway = 1,
                    updated_at = datetime('now')
                WHERE LOWER(email) = ?;
            """, (email.lower().strip(),))
            conn.commit()
    except Exception as e:
        logger.error(f"[DB] 更新 Udio 同步标记失败 ({email}): {e}")


def sync_all_unpushed_udio_accounts(
    gateway_url: str = GATEWAY_BASE_URL,
    admin_token: str = GATEWAY_ADMIN_TOKEN,
    db_path: Path | str | None = None,
) -> Tuple[int, int]:
    """批量同步所有未推送的活跃 Udio 账号至聚合网关。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    init_udio_db(p)

    with _get_sync_conn(p) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT email, access_token, refresh_token, credits, cookies_json
            FROM udio_accounts
            WHERE synced_to_gateway = 0 AND status = 'active';
        """).fetchall()

    total = len(rows)
    success = 0
    for r in rows:
        cookies = json.loads(r["cookies_json"]) if r["cookies_json"] else {}
        ok = push_account_to_music2api(
            email=r["email"],
            access_token=r["access_token"],
            refresh_token=r["refresh_token"],
            cookies=cookies,
            credits=r["credits"] or 10,
            gateway_url=gateway_url,
            admin_token=admin_token,
            db_path=p,
        )
        if ok:
            success += 1

    return success, total
