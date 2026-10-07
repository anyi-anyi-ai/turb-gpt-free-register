# -*- coding: utf-8 -*-
"""
MUSE 2 API 账号与 Cookie 自动同步模块 (Muse2API Sync Module)
对标 runninghub2api_sync.py 与 gptgrok2api_sync.py 设计：
在 turb 注册机完成 Muse (muse.ai) 账号注册并提取核心 Session Cookie 后，
自动调用独立的 muse2api 网关的 /admin/accounts 接口实现免人工全自动入库与保活。
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger("muse.sync")

_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _ROOT / "turb.sqlite3"

MUSE2API_URL = os.environ.get("MUSE2API_URL", "http://127.0.0.1:18610/admin/accounts")
MUSE2API_KEY = os.environ.get("MUSE2API_KEY", "m2a_change_me_secret_token")


def _get_sync_conn(db_path: Path | str | None = None) -> sqlite3.Connection:
    p = Path(db_path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30)
    try:
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return conn


def init_muse_db(db_path: Path | str | None = None) -> None:
    """初始化 SQLite 中的 muse_accounts 表格。"""
    with _get_sync_conn(db_path) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS muse_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE,
                password TEXT,
                full_name TEXT,
                birth_date TEXT,
                hatch_sess TEXT,
                hatch_gw TEXT,
                hatch_vml TEXT,
                hatch_native_auth_device TEXT,
                cookies_json TEXT,
                proxy_used TEXT,
                status TEXT DEFAULT 'active',
                synced_to_muse2api INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                payload_json TEXT
            );
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_muse_email ON muse_accounts(email);")
        conn.commit()


def check_muse2api_health(base_url: str | None = None, timeout: float = 3.0) -> bool:
    """检查独立部署的 muse2api 服务是否正常存活。"""
    url = (base_url or MUSE2API_URL.replace("/admin/accounts", "")).rstrip("/") + "/healthz"
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code == 200 and r.json().get("status") == "ok"
    except Exception:
        return False


def save_muse_account_to_db(
    email: str,
    cookies: Dict[str, str],
    password: str = "",
    full_name: str = "",
    birth_date: str = "",
    proxy_used: str = "",
    extra_payload: Optional[Dict[str, Any]] = None,
    db_path: Path | str | None = None,
) -> bool:
    """将注册成功的 Muse 账号及其核心凭证写入 SQLite 库。"""
    init_muse_db(db_path)
    try:
        cookies_json = json.dumps(cookies, ensure_ascii=False)
        payload_str = json.dumps(extra_payload or {}, ensure_ascii=False)
        sess = cookies.get("hatch_sess", "")
        gw = cookies.get("hatch_gw", "")
        vml = cookies.get("hatch_vml", "")
        dev = cookies.get("hatch_native_auth_device", "")

        with _get_sync_conn(db_path) as conn:
            conn.execute("""
                INSERT INTO muse_accounts (
                    email, password, full_name, birth_date,
                    hatch_sess, hatch_gw, hatch_vml, hatch_native_auth_device,
                    cookies_json, proxy_used, status, synced_to_muse2api,
                    updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', 0, CURRENT_TIMESTAMP, ?)
                ON CONFLICT(email) DO UPDATE SET
                    password = excluded.password,
                    hatch_sess = excluded.hatch_sess,
                    hatch_gw = excluded.hatch_gw,
                    hatch_vml = excluded.hatch_vml,
                    hatch_native_auth_device = excluded.hatch_native_auth_device,
                    cookies_json = excluded.cookies_json,
                    proxy_used = excluded.proxy_used,
                    updated_at = CURRENT_TIMESTAMP,
                    payload_json = excluded.payload_json
            """, (email, password, full_name, birth_date, sess, gw, vml, dev, cookies_json, proxy_used, payload_str))
            conn.commit()
        logger.info(f"💾 [MuseStore] 账号 {email} 已成功存入 SQLite (muse_accounts)")
        return True
    except Exception as e:
        logger.error(f"❌ [MuseStore] 存储账号 {email} 失败: {e}")
        return False


def push_account_to_muse2api(
    email: str,
    cookies: Dict[str, str],
    expires: Optional[Dict[str, int]] = None,
    label: str = "",
    timeout: float = 10.0,
    db_path: Path | str | None = None,
) -> bool:
    """
    单账号即时推送入库至独立运行的 muse2api 网关。
    自动校验核心 Cookie，并通过 HTTP Bearer 鉴权完成持久化注入。
    """
    headers = {
        "Authorization": f"Bearer {MUSE2API_KEY}",
        "Content-Type": "application/json",
    }
    payload: Dict[str, Any] = {
        "label": label or email,
        "cookies": cookies,
    }
    if expires:
        payload["expires"] = expires

    try:
        resp = requests.post(MUSE2API_URL, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("count", 0) > 0:
                logger.info(f"✅ [MuseSync] 账号 {email} 成功同步至 muse2api 网关 (添加数: {data.get('count')})")
                _mark_synced(email, db_path)
                return True
        logger.error(f"❌ [MuseSync] 同步到 muse2api 失败: HTTP {resp.status_code} - {resp.text}")
        return False
    except Exception as e:
        logger.error(f"❌ [MuseSync] 推送至 muse2api 网关异常 (可能网关未启动): {e}")
        return False


def _mark_synced(email: str, db_path: Path | str | None = None) -> None:
    try:
        with _get_sync_conn(db_path) as conn:
            conn.execute("UPDATE muse_accounts SET synced_to_muse2api = 1 WHERE email = ?", (email,))
            conn.commit()
    except Exception:
        pass


def push_batch_accounts_to_muse2api(
    accounts: List[Dict[str, Any]],
    timeout: float = 15.0,
    db_path: Path | str | None = None,
) -> int:
    """批量同步账号到 muse2api 网关。"""
    success_count = 0
    for acc in accounts:
        email = acc.get("email", "")
        cookies = acc.get("cookies", {})
        label = acc.get("label", email)
        expires = acc.get("expires")
        if push_account_to_muse2api(email, cookies, expires=expires, label=label, timeout=timeout, db_path=db_path):
            success_count += 1
    return success_count
