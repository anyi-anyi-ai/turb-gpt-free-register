# -*- coding: utf-8 -*-
"""GitHub 专属持久化与数据访问模块。"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Dict, Optional

from core.outlook_client import OutlookAccount

logger = logging.getLogger("github.db")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DB_PATH = _PROJECT_ROOT / "turb.sqlite3"
_OUTPUT_TXT = _PROJECT_ROOT / "accounts_github.txt"


def _db_ctx():
    from core.db.base import _sqlite_conn, _LOCK
    return _LOCK, _sqlite_conn


def init_github_table() -> None:
    """初始化 GitHub 专属账号表与索引"""
    _LOCK, _sqlite_conn = _db_ctx()
    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS github_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT NOT NULL,
                    email TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL,
                    email_password TEXT DEFAULT '',
                    cookies_json TEXT NOT NULL DEFAULT '{}',
                    proxy_used TEXT DEFAULT '',
                    fingerprint_seed TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT DEFAULT '{}'
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_gh_acc_email ON github_accounts(email);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_gh_acc_username ON github_accounts(username);")


def save_github_account(
    username: str,
    email: str,
    password: str,
    email_password: str = "",
    cookies: list[dict] | None = None,
    proxy_used: str = "",
    fingerprint_seed: str = "",
    status: str = "active",
    extra: dict | None = None,
) -> bool:
    """保存或更新已注册的 GitHub 账号数据并导出文本"""
    now_str = datetime.now(timezone.utc).isoformat()
    cookies_json = json.dumps(cookies or [], ensure_ascii=False)
    payload_json = json.dumps(extra or {}, ensure_ascii=False)

    try:
        _LOCK, _sqlite_conn = _db_ctx()
        with _LOCK, closing(_sqlite_conn()) as conn:
            with conn:
                conn.execute("""
                    INSERT INTO github_accounts (
                        username, email, password, email_password,
                        cookies_json, proxy_used, fingerprint_seed,
                        status, created_at, updated_at, payload_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(email) DO UPDATE SET
                        username=excluded.username,
                        password=excluded.password,
                        cookies_json=excluded.cookies_json,
                        proxy_used=excluded.proxy_used,
                        fingerprint_seed=excluded.fingerprint_seed,
                        status=excluded.status,
                        updated_at=excluded.updated_at,
                        payload_json=excluded.payload_json
                """, (
                    username, email, password, email_password,
                    cookies_json, proxy_used, fingerprint_seed,
                    status, now_str, now_str, payload_json
                ))

        # 追加写入本地导出文件
        line = f"{username}----{password}----{email}----{email_password}----{now_str}\n"
        with open(_OUTPUT_TXT, "a", encoding="utf-8") as f:
            f.write(line)

        logger.info(f"💾 [持久化] 账号 {username} ({email}) 已保存入库并写入 accounts_github.txt")
        return True
    except Exception as e:
        logger.error(f"❌ 持久化 GitHub 账号异常: {e}")
        return False


def load_pending_accounts(source_file: str | None = None) -> list[OutlookAccount]:
    """读取待注册的邮箱素材 (优先 SQLite 邮箱池，其次文件兜底)"""
    accounts: list[OutlookAccount] = []

    try:
        _LOCK, _sqlite_conn = _db_ctx()
        with _LOCK, closing(_sqlite_conn()) as conn:
            rows = conn.execute("""
                SELECT id, email, payload FROM email_pool
                WHERE source='outlook'
                  AND archived=0
                  AND status NOT IN ('failed', 'banned')
                  AND email NOT IN (SELECT email FROM github_accounts WHERE status != 'failed' COLLATE NOCASE)
                ORDER BY id ASC
            """).fetchall()

            for r in rows:
                try:
                    p = json.loads(r["payload"])
                    cid = p.get("client_id") or ""
                    rt = p.get("refresh_token") or ""
                    pwd = p.get("password") or ""
                    if cid and rt:
                        accounts.append(OutlookAccount(
                            email=r["email"],
                            password=pwd,
                            client_id=cid,
                            refresh_token=rt,
                        ))
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"[邮箱池] 读取 SQLite 异常: {e}")

    # 文件兜底
    if not accounts:
        candidate_paths = [
            Path(source_file) if source_file else None,
            _PROJECT_ROOT / "用于注册的邮箱.txt",
            _PROJECT_ROOT / "用于注册的邮箱.txt.example",
        ]
        for p in candidate_paths:
            if p and p.exists():
                logger.info(f"[邮箱池] 正在从文本加载素材: {p.name}")
                with open(p, "r", encoding="utf-8", errors="replace") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split("----")
                        if len(parts) >= 4:
                            accounts.append(OutlookAccount(
                                email=parts[0].strip(),
                                password=parts[1].strip(),
                                client_id=parts[2].strip(),
                                refresh_token=parts[3].strip(),
                            ))
                if accounts:
                    break

    return accounts
