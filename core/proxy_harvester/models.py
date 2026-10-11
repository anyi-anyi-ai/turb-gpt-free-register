# -*- coding: utf-8 -*-
"""Proxy Harvester 数据模型与 SQLite 持久化层。"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

logger = logging.getLogger("harvester.models")
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "turb.sqlite3"


@dataclass
class ProxyAccountRecord:
    email: str
    account_password: str
    email_source: str = "outlook"  # outlook, uned, cf_custom
    sub_user: str = ""             # 平台下发的子账号，如 rp4xl3zwavs1
    sub_pass: str = ""             # 平台下发的密码，如 kwnf5zezha06i5f
    api_token: str = ""            # 平台 API Token / Download Token
    node_count: int = 0
    proxies: list[str] = field(default_factory=list)
    batch_id: str = ""
    status: str = "active"
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    expires_at: str = field(
        default_factory=lambda: (datetime.now() + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
    )


@dataclass
class HarvestBatch:
    batch_id: str
    total_accounts: int
    total_nodes: int
    file_path: str
    synced_to_resin: bool = False
    created_at: str = field(default_factory=lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S"))


def init_harvester_db(db_path: Path | str | None = None) -> None:
    """初始化代理采集账本数据表。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS harvested_proxy_batches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT UNIQUE NOT NULL,
                total_accounts INTEGER NOT NULL,
                total_nodes INTEGER NOT NULL,
                file_path TEXT NOT NULL,
                synced_to_resin INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            );
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS harvested_proxy_accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                batch_id TEXT NOT NULL,
                email TEXT NOT NULL,
                email_source TEXT NOT NULL,
                account_password TEXT NOT NULL,
                sub_user TEXT,
                sub_pass TEXT,
                api_token TEXT,
                node_count INTEGER DEFAULT 0,
                proxies_json TEXT,
                status TEXT DEFAULT 'active',
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
        """)
        conn.commit()


def save_account_record(rec: ProxyAccountRecord, db_path: Path | str | None = None) -> int:
    """保存单个已成功采集的代理账号记录。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    init_harvester_db(p)
    with sqlite3.connect(p) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO harvested_proxy_accounts (
                batch_id, email, email_source, account_password,
                sub_user, sub_pass, api_token, node_count,
                proxies_json, status, created_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
            """,
            (
                rec.batch_id,
                rec.email,
                rec.email_source,
                rec.account_password,
                rec.sub_user,
                rec.sub_pass,
                rec.api_token,
                rec.node_count,
                json.dumps(rec.proxies, ensure_ascii=False),
                rec.status,
                rec.created_at,
                rec.expires_at,
            ),
        )
        conn.commit()
        return cur.lastrowid or 0


def save_batch_record(batch: HarvestBatch, db_path: Path | str | None = None) -> int:
    """保存或更新采集批次元数据。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    init_harvester_db(p)
    with sqlite3.connect(p) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO harvested_proxy_batches (
                batch_id, total_accounts, total_nodes, file_path, synced_to_resin, created_at
            ) VALUES (?, ?, ?, ?, ?, ?);
            """,
            (
                batch.batch_id,
                batch.total_accounts,
                batch.total_nodes,
                batch.file_path,
                1 if batch.synced_to_resin else 0,
                batch.created_at,
            ),
        )
        conn.commit()
        return cur.lastrowid or 0


def mark_batch_synced(batch_id: str, db_path: Path | str | None = None) -> None:
    """更新批次为已同步至 Resin。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    with sqlite3.connect(p) as conn:
        conn.execute(
            "UPDATE harvested_proxy_batches SET synced_to_resin = 1 WHERE batch_id = ?",
            (batch_id,),
        )
        conn.commit()
