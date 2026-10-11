# -*- coding: utf-8 -*-
"""
RunningHub 2 API 账号与代理自动同步模块
对标 gptgrok2api_sync.py 设计：
在 turb 注册机完成 RunningHub 账号注册并获取 Token 后，
自动调用 RunningHub2API 网关的 /api/accounts/import 接口实现全自动入库。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
import requests
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB_PATH = _ROOT / "turb.sqlite3"
RH2API_IMPORT_URL = os.environ.get("RH2API_IMPORT_URL", "http://127.0.0.1:8788/api/accounts/import")


def init_runninghub_db(db_path: Path | str | None = None) -> None:
    p = Path(db_path or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as conn:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA busy_timeout=30000;")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS runninghub_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password TEXT,
                token TEXT NOT NULL,
                user_id TEXT,
                credits INTEGER DEFAULT 1000,
                invite_code TEXT,
                cookies_json TEXT,
                proxy_used TEXT,
                status TEXT DEFAULT 'active',
                synced_to_gateway INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                payload_json TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_rh_email ON runninghub_accounts(email);")
        conn.commit()


def save_runninghub_account_to_db(
    email: str,
    token: str,
    password: str = "",
    user_id: str = "",
    credits: int = 1000,
    invite_code: str = "",
    cookies: Optional[Dict[str, str]] = None,
    proxy_used: str = "",
    payload: Optional[Dict[str, Any]] = None,
    db_path: Path | str | None = None,
) -> bool:
    p = Path(db_path or DEFAULT_DB_PATH)
    init_runninghub_db(p)
    cookies_str = json.dumps(cookies or {}, ensure_ascii=False)
    payload_str = json.dumps(payload or {}, ensure_ascii=False)

    try:
        with sqlite3.connect(p) as conn:
            conn.execute("PRAGMA busy_timeout=30000;")
            conn.execute("""
                INSERT INTO runninghub_accounts (
                    email, password, token, user_id, credits, invite_code,
                    cookies_json, proxy_used, status, synced_to_gateway,
                    created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(email) DO UPDATE SET
                    token = excluded.token,
                    user_id = excluded.user_id,
                    credits = excluded.credits,
                    cookies_json = excluded.cookies_json,
                    proxy_used = excluded.proxy_used,
                    status = 'active',
                    updated_at = CURRENT_TIMESTAMP,
                    payload_json = excluded.payload_json;
            """, (
                email.strip(),
                password.strip(),
                token.strip(),
                str(user_id).strip(),
                credits,
                invite_code.strip(),
                cookies_str,
                proxy_used.strip(),
                payload_str,
            ))
            conn.commit()
            logger.info(f"[DB] RunningHub 账号 {email} 已成功持久化至 {p.name} (runninghub_accounts 表)")
            return True
    except Exception as e:
        logger.error(f"[DB] 持久化 RunningHub 账号异常: {e}")
        return False



def push_account_to_rh2api(
    token: str,
    user_id: str,
    email: str = "",
    proxy: str = "",
    total_coin: int = 1000,
    remark: str = "UNED 注册机自动产出",
    timeout: float = 10.0,
) -> bool:
    """
    单账号即时推送入库
    """
    payload = {
        "token": token.strip(),
        "userId": str(user_id).strip(),
        "email": email.strip(),
        "proxy": proxy.strip(),
        "total_coin": total_coin,
        "remark": remark,
    }

    try:
        resp = requests.post(RH2API_IMPORT_URL, json=payload, timeout=timeout)
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"✅ 账号 {email or user_id} 成功同步至 RunningHub2API 网关")
            return True
        else:
            logger.error(f"❌ 同步失败: HTTP {resp.status_code} - {resp.text}")
            return False
    except Exception as e:
        logger.error(f"❌ 推送至 RunningHub2API 异常 (可能网关未启动): {e}")
        return False


def push_batch_accounts_to_rh2api(accounts: List[Dict[str, Any]], timeout: float = 15.0) -> bool:
    """
    批量账号推送入库
    """
    if not accounts:
        return True

    try:
        resp = requests.post(RH2API_IMPORT_URL, json=accounts, timeout=timeout)
        if resp.status_code == 200 and resp.json().get("ok"):
            logger.info(f"✅ 成功批量同步 {len(accounts)} 个账号至 RunningHub2API 网关")
            return True
        else:
            logger.error(f"❌ 批量同步失败: {resp.text}")
            return False
    except Exception as e:
        logger.error(f"❌ 批量推送至 RunningHub2API 异常: {e}")
        return False
