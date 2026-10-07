# -*- coding: utf-8 -*-
"""Concurrency tests for atomic email pool claiming."""
import concurrent.futures
import json
import sqlite3
import tempfile
from contextlib import closing
from unittest.mock import patch

from core.db import email_pool


def test_atomic_email_claim_concurrency(tmp_path):
    db_file = str(tmp_path / "test_turb.sqlite3")

    # 初始化测试表结构并插入一条可用邮箱
    with sqlite3.connect(db_file) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_pool (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE COLLATE NOCASE,
                source TEXT,
                status TEXT,
                archived INTEGER DEFAULT 0,
                created_at TEXT,
                updated_at TEXT,
                payload TEXT
            )
        """)
        init_payload = json.dumps({"email": "winner@test.com", "status": "available", "source": "outlook"})
        conn.execute(
            "INSERT INTO email_pool (email, source, status, archived, payload) VALUES (?, ?, ?, 0, ?)",
            ("winner@test.com", "outlook", "available", init_payload),
        )

    from pathlib import Path
    with patch("core.db.base._active_sqlite_path", return_value=Path(db_file)), \
         patch("core.db.base._ensure_sqlite", return_value=None), \
         patch("core.db.email_pool._ensure_sqlite", return_value=None):
        def _worker():
            return email_pool._claim_next_email_from_source("outlook", for_service="chatgpt")

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            futures = [executor.submit(_worker) for _ in range(8)]
        results = [f.result() for f in futures]

    claims = [r for r in results if r is not None]
    assert len(claims) == 1, f"Expected exactly 1 claim, got {len(claims)}"
    assert claims[0]["email"] == "winner@test.com"
    assert claims[0]["status"] == "used"

    # 验证库中记录状态
    with sqlite3.connect(db_file) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT status, payload FROM email_pool WHERE email='winner@test.com'").fetchone()
        assert row["status"] == "used"
        payload = json.loads(row["payload"])
        assert payload["status"] == "used"
        assert payload.get("used_at") is not None
