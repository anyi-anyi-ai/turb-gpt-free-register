# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any

from .base import _LOCK, _now, _sqlite_conn, _ensure_sqlite


def _ensure_uned_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS uned_accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uned_identifier TEXT NOT NULL UNIQUE,
            email TEXT NOT NULL,
            phone TEXT NOT NULL,
            doc_type TEXT NOT NULL,
            doc_number TEXT NOT NULL,
            full_name TEXT NOT NULL,
            birth_date TEXT NOT NULL,
            nationality TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'registered',
            proxy_used TEXT NOT NULL DEFAULT '',
            payload_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_uned_accounts_email ON uned_accounts(email)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_uned_accounts_doc ON uned_accounts(doc_number)")


def insert_uned_account(
    *,
    uned_identifier: str,
    email: str,
    phone: str,
    doc_type: str,
    doc_number: str,
    full_name: str,
    birth_date: str = "",
    nationality: str = "España",
    status: str = "registered",
    proxy_used: str = "",
    extra: dict[str, Any] | None = None,
) -> int:
    """插入或更新 UNED 账号记录。"""
    _ensure_sqlite()
    now_str = _now()
    payload = {
        "uned_identifier": uned_identifier,
        "email": email,
        "phone": phone,
        "doc_type": doc_type,
        "doc_number": doc_number,
        "full_name": full_name,
        "birth_date": birth_date,
        "nationality": nationality,
        "status": status,
        "proxy_used": proxy_used,
        "extra": extra or {},
        "created_at": now_str,
        "updated_at": now_str,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    with _LOCK, closing(_sqlite_conn()) as conn:
        _ensure_uned_table(conn)
        with conn:
            cur = conn.execute(
                """
                INSERT INTO uned_accounts (
                    uned_identifier, email, phone, doc_type, doc_number, full_name,
                    birth_date, nationality, status, proxy_used, payload_json,
                    created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uned_identifier) DO UPDATE SET
                    email = excluded.email,
                    phone = excluded.phone,
                    doc_type = excluded.doc_type,
                    doc_number = excluded.doc_number,
                    full_name = excluded.full_name,
                    birth_date = excluded.birth_date,
                    nationality = excluded.nationality,
                    status = excluded.status,
                    proxy_used = excluded.proxy_used,
                    payload_json = excluded.payload_json,
                    updated_at = excluded.updated_at
                """,
                (
                    uned_identifier,
                    email,
                    phone,
                    doc_type,
                    doc_number,
                    full_name,
                    birth_date,
                    nationality,
                    status,
                    proxy_used,
                    payload_json,
                    now_str,
                    now_str,
                ),
            )
            return cur.lastrowid or 0


def list_uned_accounts(limit: int = 100) -> list[dict[str, Any]]:
    """列出 UNED 账号列表。"""
    _ensure_sqlite()
    with _LOCK, closing(_sqlite_conn()) as conn:
        _ensure_uned_table(conn)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT * FROM uned_accounts ORDER BY id DESC LIMIT ?", (limit,))
        rows = cur.fetchall()
        result = []
        for r in rows:
            item = dict(r)
            try:
                item["payload"] = json.loads(item.get("payload_json") or "{}")
            except Exception:
                item["payload"] = {}
            result.append(item)
        return result
