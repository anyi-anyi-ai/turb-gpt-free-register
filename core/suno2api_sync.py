# -*- coding: utf-8 -*-
"""
Suno 2 API 账号与 Cookie 自动同步模块 (Suno2API Sync Module)
架构解耦设计：
在 turb 注册机完成 Suno (suno.com) 账号自动注册并提取核心 Clerk 凭证（__client, __session 等）后，
自动调用独立的下游网关（如 suno2api / music2api）的接口实现免人工全自动入库与凭据流转。
若网关未启动，凭证安全落盘本地 SQLite，支持后续批量离线回补。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("suno.sync")

_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _ROOT / "turb.sqlite3"

SUNO2API_URL = os.environ.get("SUNO2API_URL", "http://127.0.0.1:18620/admin/accounts")
SUNO2API_KEY = os.environ.get("SUNO2API_KEY", "suno_change_me_secret_token")


def _get_sync_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(db_path or DB_PATH)
    conn = sqlite3.connect(str(p), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


def init_suno_db(db_path: Path | str | None = None) -> None:
    """初始化 SQLite 中的 suno_accounts 数据表。"""
    conn = _get_sync_conn(db_path)
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS suno_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password TEXT,
                clerk_user_id TEXT,
                clerk_client_id TEXT,
                clerk_session_id TEXT,
                session_jwt TEXT,
                cookies_json TEXT,
                proxy_used TEXT,
                status TEXT DEFAULT 'active',
                synced_to_gateway INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                payload_json TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_suno_email ON suno_accounts(email);")
        conn.commit()
    finally:
        conn.close()


def check_suno2api_health(base_url: str | None = None, timeout: float = 3.0) -> bool:
    """检查下游独立部署的 suno2api 网关服务是否存活。"""
    url = (base_url or SUNO2API_URL.replace("/admin/accounts", "")).rstrip("/") + "/healthz"
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code == 200 and r.json().get("status") in ("ok", "healthy", "up")
    except Exception:
        return False


def save_suno_account_to_db(
    email: str,
    cookies: Dict[str, str],
    password: str = "",
    clerk_user_id: str = "",
    clerk_client_id: str = "",
    clerk_session_id: str = "",
    session_jwt: str = "",
    proxy_used: str = "",
    extra_payload: Optional[Dict[str, Any]] = None,
    db_path: Path | str | None = None,
) -> bool:
    """将注册成功的 Suno 账号及其核心凭证安全存入 SQLite 本地库。"""
    init_suno_db(db_path)
    conn = _get_sync_conn(db_path)
    try:
        cookies_json = json.dumps(cookies, ensure_ascii=False)
        payload_str = json.dumps(extra_payload or {}, ensure_ascii=False)

        # 提取关键 Clerk Cookie 兜底
        if not clerk_client_id and "__client" in cookies:
            clerk_client_id = cookies["__client"]
        if not session_jwt and "__session" in cookies:
            session_jwt = cookies["__session"]

        conn.execute("""
            INSERT INTO suno_accounts (
                email, password, clerk_user_id, clerk_client_id, clerk_session_id,
                session_jwt, cookies_json, proxy_used, status, synced_to_gateway,
                updated_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, CURRENT_TIMESTAMP, ?)
            ON CONFLICT(email) DO UPDATE SET
                password = excluded.password,
                clerk_user_id = excluded.clerk_user_id,
                clerk_client_id = excluded.clerk_client_id,
                clerk_session_id = excluded.clerk_session_id,
                session_jwt = excluded.session_jwt,
                cookies_json = excluded.cookies_json,
                proxy_used = excluded.proxy_used,
                updated_at = CURRENT_TIMESTAMP,
                payload_json = excluded.payload_json
        """, (
            email, password, clerk_user_id, clerk_client_id, clerk_session_id,
            session_jwt, cookies_json, proxy_used, payload_str
        ))
        conn.commit()
        logger.info(f"💾 [SunoStore] 账号 {email} 已成功持久化至 SQLite (suno_accounts)")
        return True
    except Exception as e:
        logger.error(f"❌ [SunoStore] 持久化账号 {email} 失败: {e}")
        return False
    finally:
        conn.close()


def push_account_to_suno2api(
    email: str,
    cookies: Dict[str, str],
    session_jwt: str = "",
    clerk_client_id: str = "",
    label: str = "",
    timeout: float = 10.0,
    db_path: Path | str | None = None,
) -> bool:
    """
    单账号即时解耦推送：将 Suno 凭据推送至下游网关。
    下发格式标准化，包含 Clerk Cookie 串与长效 Client Token。
    """
    headers = {
        "Authorization": f"Bearer {SUNO2API_KEY}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "provider": "suno",
        "email": email,
        "label": label or email,
        "cookies": cookies,
        "credentials": {
            "session_jwt": session_jwt or cookies.get("__session", ""),
            "client_token": clerk_client_id or cookies.get("__client", ""),
        }
    }

    try:
        resp = requests.post(SUNO2API_URL, json=payload, headers=headers, timeout=timeout)
        if resp.status_code in (200, 201):
            data = resp.json()
            logger.info(f"✅ [SunoSync] 账号 {email} 成功同步至网关: {data}")
            _mark_synced(email, db_path)
            return True
        logger.error(f"❌ [SunoSync] 同步到网关失败: HTTP {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        logger.error(f"❌ [SunoSync] 推送至网关异常 (网关未启动或网络不通，凭证已由 SQLite 安全暂存): {e}")
        return False


def _mark_synced(email: str, db_path: Path | str | None = None) -> None:
    try:
        with _get_sync_conn(db_path) as conn:
            conn.execute("UPDATE suno_accounts SET synced_to_gateway = 1 WHERE email = ?", (email,))
            conn.commit()
    except Exception:
        pass


def push_batch_accounts_to_suno2api(
    accounts: List[Dict[str, Any]],
    timeout: float = 15.0,
    db_path: Path | str | None = None,
) -> int:
    """批量同步账号到下游网关。"""
    success_count = 0
    for acc in accounts:
        email = acc.get("email", "")
        cookies = acc.get("cookies", {})
        jwt = acc.get("session_jwt", "")
        client_id = acc.get("clerk_client_id", "")
        label = acc.get("label", email)
        if push_account_to_suno2api(
            email=email,
            cookies=cookies,
            session_jwt=jwt,
            clerk_client_id=client_id,
            label=label,
            timeout=timeout,
            db_path=db_path,
        ):
            success_count += 1
    return success_count
