# -*- coding: utf-8 -*-
"""Kiro (AWS Builder ID) 专属数据访问层 (DAO)。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import _LOCK, _now, _sqlite_conn, _ensure_sqlite


def init_kiro_table() -> None:
    """初始化 Kiro 专属账号表与索引。"""
    _ensure_sqlite()
    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS kiro_accounts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT NOT NULL UNIQUE,
                    password TEXT NOT NULL,
                    start_url TEXT DEFAULT '',
                    sso_token TEXT DEFAULT '',
                    refresh_token TEXT DEFAULT '',
                    client_id TEXT DEFAULT '',
                    client_secret TEXT DEFAULT '',
                    expires_at TEXT DEFAULT '',
                    proxy_used TEXT DEFAULT '',
                    status TEXT DEFAULT 'active',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    payload_json TEXT DEFAULT '{}'
                );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kiro_acc_email ON kiro_accounts(email);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_kiro_acc_status ON kiro_accounts(status);")


def insert_kiro_account(
    *,
    email: str,
    password: str,
    start_url: str = "",
    sso_token: str = "",
    refresh_token: str = "",
    client_id: str = "",
    client_secret: str = "",
    expires_at: str = "",
    proxy_used: str = "",
    status: str = "active",
    extra: dict | None = None,
) -> int:
    """插入或更新 Kiro 账号记录。"""
    init_kiro_table()
    now_str = _now()
    payload_json = json.dumps(extra or {}, ensure_ascii=False)

    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            cur = conn.execute("""
                INSERT INTO kiro_accounts (
                    email, password, start_url, sso_token, refresh_token,
                    client_id, client_secret, expires_at, proxy_used,
                    status, created_at, updated_at, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(email) DO UPDATE SET
                    password=excluded.password,
                    start_url=excluded.start_url,
                    sso_token=excluded.sso_token,
                    refresh_token=excluded.refresh_token,
                    client_id=excluded.client_id,
                    client_secret=excluded.client_secret,
                    expires_at=excluded.expires_at,
                    proxy_used=excluded.proxy_used,
                    status=excluded.status,
                    updated_at=excluded.updated_at,
                    payload_json=excluded.payload_json
            """, (
                email, password, start_url, sso_token, refresh_token,
                client_id, client_secret, expires_at, proxy_used,
                status, now_str, now_str, payload_json
            ))
            return cur.lastrowid or 0


def get_kiro_account_by_email(email: str) -> dict[str, Any] | None:
    """按邮箱查询 Kiro 账号记录。"""
    init_kiro_table()
    with _LOCK, closing(_sqlite_conn()) as conn:
        row = conn.execute("""
            SELECT id, email, password, start_url, sso_token, refresh_token,
                   client_id, client_secret, expires_at, proxy_used, status,
                   created_at, updated_at, payload_json
            FROM kiro_accounts WHERE email=?
        """, (email,)).fetchone()
        if not row:
            return None
        return {
            "id": row[0],
            "email": row[1],
            "password": row[2],
            "start_url": row[3],
            "sso_token": row[4],
            "refresh_token": row[5],
            "client_id": row[6],
            "client_secret": row[7],
            "expires_at": row[8],
            "proxy_used": row[9],
            "status": row[10],
            "created_at": row[11],
            "updated_at": row[12],
            "payload": json.loads(row[13] or "{}"),
        }


def list_kiro_accounts(limit: int = 100, offset: int = 0, status: str | None = None) -> list[dict[str, Any]]:
    """分页列出 Kiro 账号清单。"""
    init_kiro_table()
    with _LOCK, closing(_sqlite_conn()) as conn:
        if status:
            rows = conn.execute("""
                SELECT id, email, password, start_url, sso_token, refresh_token,
                       client_id, expires_at, status, created_at
                FROM kiro_accounts WHERE status=? ORDER BY id DESC LIMIT ? OFFSET ?
            """, (status, limit, offset)).fetchall()
        else:
            rows = conn.execute("""
                SELECT id, email, password, start_url, sso_token, refresh_token,
                       client_id, expires_at, status, created_at
                FROM kiro_accounts ORDER BY id DESC LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()

        return [
            {
                "id": r[0], "email": r[1], "password": r[2], "start_url": r[3],
                "sso_token": r[4], "refresh_token": r[5], "client_id": r[6],
                "expires_at": r[7], "status": r[8], "created_at": r[9]
            }
            for r in rows
        ]
