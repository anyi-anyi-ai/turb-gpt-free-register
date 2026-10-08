# -*- coding: utf-8 -*-
"""GitHub 专属数据访问层适配器 (DAO)。"""
from __future__ import annotations

import json
import sqlite3
from typing import Any
from pathlib import Path

from contextlib import closing
from core.db.base import _sqlite_conn, _LOCK
from core.github.db import (
    init_github_table,
    save_github_account,
    load_pending_accounts,
    _DB_PATH,
)


def get_github_account(email: str) -> dict[str, Any] | None:
    """按邮箱查询 GitHub 账号记录。"""
    init_github_table()
    with _LOCK, closing(_sqlite_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM github_accounts WHERE lower(email)=?",
            (email.strip().lower(),)
        ).fetchone()
        if not row:
            return None
        d = dict(row)
        try:
            d["cookies"] = json.loads(d.get("cookies_json") or "[]")
        except Exception:
            d["cookies"] = []
        try:
            d["payload"] = json.loads(d.get("payload_json") or "{}")
        except Exception:
            d["payload"] = {}
        return d


def list_github_accounts(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    """分页列出 GitHub 账号清单。"""
    init_github_table()
    with _LOCK, closing(_sqlite_conn()) as conn:
        rows = conn.execute(
            "SELECT id, username, email, password, status, created_at, proxy_used "
            "FROM github_accounts ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        ).fetchall()
        return [dict(r) for r in rows]


__all__ = [
    "init_github_table",
    "save_github_account",
    "get_github_account",
    "list_github_accounts",
    "load_pending_accounts",
]

