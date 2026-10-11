# -*- coding: utf-8 -*-
"""Outlook 邮箱与 Graph Token 生命周期管理模块 (DAO)。"""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import _LOCK, _now, _sqlite_conn, _ensure_sqlite, _outlook_line


def upsert_outlook_with_graph(
    *,
    email: str,
    password: str,
    refresh_token: str,
    client_id: str = "9e5f94bc-e8a4-4e73-b8be-63364c29d753",
    recovery_email: str = "",
    recovery_code: str = "",
    status: str = "available",
    note: str = "",
) -> int:
    """插入或更新已取得 Graph API Refresh Token 的 Outlook 账号记录。"""
    _ensure_sqlite()
    now_str = _now()
    email_clean = email.strip().lower()

    payload_dict = {
        "email": email.strip(),
        "password": password.strip(),
        "clientId": client_id.strip(),
        "refreshToken": refresh_token.strip(),
        "recoveryEmail": recovery_email.strip(),
        "recoveryCode": recovery_code.strip(),
        "status": status,
        "note": note,
        "updated_at": now_str,
    }
    payload_dict["copy_line"] = _outlook_line(payload_dict)
    payload_json = json.dumps(payload_dict, ensure_ascii=False)

    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            # 检查是否已在 email_pool 中存在该邮箱
            row = conn.execute(
                "SELECT id, payload FROM email_pool WHERE lower(email)=? AND source='outlook'",
                (email_clean,)
            ).fetchone()

            if row:
                row_id = row[0]
                existing_payload = json.loads(row[1] or "{}")
                existing_payload.update(payload_dict)
                existing_payload["copy_line"] = _outlook_line(existing_payload)
                conn.execute(
                    "UPDATE email_pool SET status=?, updated_at=?, payload=? WHERE id=?",
                    (status, now_str, json.dumps(existing_payload, ensure_ascii=False), row_id)
                )
                return row_id
            else:
                cur = conn.execute(
                    "INSERT INTO email_pool (email, source, status, archived, created_at, updated_at, payload) "
                    "VALUES (?, 'outlook', ?, 0, ?, ?, ?)",
                    (email.strip(), status, now_str, now_str, payload_json)
                )
                return cur.lastrowid or 0


def update_outlook_status(email: str, status: str, note: str = "") -> bool:
    """更新 Outlook 账号状态（例如 locked, banned, expired, available）。"""
    _ensure_sqlite()
    now_str = _now()
    email_clean = email.strip().lower()

    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            row = conn.execute(
                "SELECT id, payload FROM email_pool WHERE lower(email)=? AND source='outlook'",
                (email_clean,)
            ).fetchone()
            if not row:
                return False
            row_id, payload_str = row
            payload = json.loads(payload_str or "{}")
            payload["status"] = status
            if note:
                payload["note"] = note
            payload["updated_at"] = now_str
            conn.execute(
                "UPDATE email_pool SET status=?, updated_at=?, payload=? WHERE id=?",
                (status, now_str, json.dumps(payload, ensure_ascii=False), row_id)
            )
            return True


def list_outlook_locked_candidates() -> list[dict[str, Any]]:
    """查询标记为 locked 或异常需要解锁的 Outlook 账号列表。"""
    _ensure_sqlite()
    with _LOCK, closing(_sqlite_conn()) as conn:
        rows = conn.execute(
            "SELECT email, payload FROM email_pool WHERE source='outlook' AND status IN ('locked', 'banned', 'expired') "
            "ORDER BY id DESC"
        ).fetchall()
        result = []
        for r in rows:
            data = json.loads(r[1] or "{}")
            result.append(data)
        return result
