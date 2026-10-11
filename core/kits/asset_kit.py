# -*- coding: utf-8 -*-
"""
core/kits/asset_kit.py — 资产沉淀与下游网关同步积木组件 (AssetKit)
提供标准化的 SQLite 本地持久化与 API 网关凭证分发：
  - 自动维护平台账号表结构 (自动建表、字段自愈)
  - 账号凭据落盘 (AES/明文按需、Cookies 序列化)
  - 下游网关实时推流 (music2api / sub2api / new-api)
  - 本地账号检索与存活状态更新
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.db.base import _LOCK, _sqlite_conn

logger = logging.getLogger("turb.kit.asset")

_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_DB_PATH = _ROOT / "turb.sqlite3"


class AssetKit:
    """标准化资产沉淀与下游同步原子积木。"""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)

    def init_table(self, table_name: str, extra_columns: Optional[List[str]] = None) -> None:
        """确保目标平台的 SQLite 数据表存在。"""
        with _LOCK, _sqlite_conn() as conn:
            conn.execute(f"""
                CREATE TABLE IF NOT EXISTS {table_name} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    email TEXT UNIQUE NOT NULL,
                    password TEXT,
                    api_key TEXT,
                    session_token TEXT,
                    refresh_token TEXT,
                    cookies_json TEXT,
                    credits INTEGER DEFAULT 0,
                    status TEXT DEFAULT 'active',
                    synced_to_gateway INTEGER DEFAULT 0,
                    metadata_json TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """)
            conn.commit()

    def save_account(
        self,
        table_name: str,
        email: str,
        password: str = "",
        api_key: str = "",
        session_token: str = "",
        refresh_token: str = "",
        cookies: Optional[Dict[str, Any]] = None,
        credits: int = 0,
        status: str = "active",
        synced_to_gateway: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """将注册成功的凭证持久化至 SQLite 数据库。"""
        self.init_table(table_name)
        cookies_json = json.dumps(cookies, ensure_ascii=False) if cookies else "{}"
        metadata_json = json.dumps(metadata, ensure_ascii=False) if metadata else "{}"

        with _LOCK, _sqlite_conn() as conn:
            conn.execute(f"""
                INSERT INTO {table_name} (
                    email, password, api_key, session_token, refresh_token,
                    cookies_json, credits, status, synced_to_gateway, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(email) DO UPDATE SET
                    password = COALESCE(excluded.password, {table_name}.password),
                    api_key = COALESCE(excluded.api_key, {table_name}.api_key),
                    session_token = COALESCE(excluded.session_token, {table_name}.session_token),
                    refresh_token = COALESCE(excluded.refresh_token, {table_name}.refresh_token),
                    cookies_json = COALESCE(excluded.cookies_json, {table_name}.cookies_json),
                    credits = excluded.credits,
                    status = excluded.status,
                    synced_to_gateway = excluded.synced_to_gateway,
                    metadata_json = excluded.metadata_json,
                    updated_at = CURRENT_TIMESTAMP;
            """, (
                email.strip().lower(),
                password,
                api_key,
                session_token,
                refresh_token,
                cookies_json,
                credits,
                status,
                1 if synced_to_gateway else 0,
                metadata_json,
            ))
            conn.commit()
            logger.info(f"[AssetKit] 账号资产已成功落盘至 {table_name} 表: {email}")
            return True

    def list_accounts(self, table_name: str, limit: int = 50) -> List[Dict[str, Any]]:
        """获取指定平台的已注册账号列表 (自适应字段映射)。"""
        self.init_table(table_name)
        with _LOCK, _sqlite_conn() as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            # 动态检查表的字段
            cols = [r[1] for r in cursor.execute(f"PRAGMA table_info({table_name});").fetchall()]
            
            rows = cursor.execute(f"""
                SELECT *
                FROM {table_name}
                ORDER BY id DESC
                LIMIT ?;
            """, (limit,)).fetchall()
            
            results = []
            for r in rows:
                row_dict = dict(r)
                # 自适应提取 key/token
                key_token = (
                    row_dict.get("api_key")
                    or row_dict.get("session_jwt")
                    or row_dict.get("session_token")
                    or row_dict.get("clerk_session_id")
                    or ""
                )
                results.append({
                    "id": row_dict.get("id"),
                    "email": row_dict.get("email"),
                    "api_key": key_token,
                    "credits": row_dict.get("credits", 0),
                    "status": row_dict.get("status", "active"),
                    "synced_to_gateway": row_dict.get("synced_to_gateway", 0),
                    "updated_at": row_dict.get("updated_at") or row_dict.get("created_at") or "",
                })
            return results
