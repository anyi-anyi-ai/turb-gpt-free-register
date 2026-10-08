# -*- coding: utf-8 -*-
"""
core/flows/pool_helper.py — 跨平台邮箱池分配与去重通用工具。
统一接入主库 WAL 连接与线程锁，消除各平台注册服务内重复的 SQL 查询。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, Optional

from core.db.base import _LOCK, _sqlite_conn

logger = logging.getLogger("flow.pool")

_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = _ROOT / "turb.sqlite3"


def pick_unregistered_email_for_platform(
    platform_table: str,
    specific_email: Optional[str] = None,
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """
    从本地 SQLite 邮箱池中分配未在指定平台注册过的可用邮箱。
    
    :param platform_table: 对应平台的已注册账号表名（例如 'udio_accounts', 'suno_accounts'）
    :param specific_email: 指定领取的特定邮箱（可选）
    :param db_path: 数据库文件路径（可选，默认 turb.sqlite3）
    :return: 包含 email, payload, source 的字典
    """
    if db_path:
        conn_target = Path(db_path)
        conn = sqlite3.connect(str(conn_target), timeout=30)
        try:
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA journal_mode=WAL")
        except Exception:
            pass
        ctx = conn
    else:
        ctx = _sqlite_conn()

    with _LOCK, ctx as conn:
        if specific_email:
            row = conn.execute(
                "SELECT email, payload FROM email_pool WHERE LOWER(email) = ? LIMIT 1;",
                (specific_email.lower().strip(),),
            ).fetchone()
            if row:
                payload = json.loads(row[1]) if row[1] else {}
                return {
                    "email": row[0],
                    "payload": payload,
                    "source": "manual",
                }
            return {
                "email": specific_email.strip(),
                "payload": {},
                "source": "manual",
            }

        # 查找不在该平台表中的可用邮箱
        query = f"""
            SELECT ep.email, ep.payload
            FROM email_pool ep
            WHERE LOWER(ep.email) NOT IN (SELECT LOWER(email) FROM {platform_table})
            ORDER BY ep.id ASC
            LIMIT 50;
        """
        rows = conn.execute(query).fetchall()

        for row in rows:
            email = row[0]
            payload = json.loads(row[1]) if row[1] else {}
            return {
                "email": email,
                "payload": payload,
                "source": "email_pool",
            }

    raise RuntimeError(f"本地邮箱池中已无未注册 {platform_table} 的可用邮箱，请先导入新邮箱。")
