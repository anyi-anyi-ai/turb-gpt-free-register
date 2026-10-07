# -*- coding: utf-8 -*-
import sqlite3
import pytest
from pathlib import Path

from core.udio2api_sync import init_udio_db, save_udio_account_to_db, _get_sync_conn as udio_conn
from core.elevenlabs2api_sync import init_elevenlabs_db, save_elevenlabs_account_to_db, _get_sync_conn as el_conn
from core.muse2api_sync import init_muse_db, save_muse_account_to_db, _get_sync_conn as muse_conn
from core.stableaudio2api_sync import init_stableaudio_db, save_stableaudio_account_to_db, _get_sync_conn as sa_conn


def test_sync_daos_wal_and_save(tmp_path: Path):
    db_file = tmp_path / "test_sync.sqlite3"

    # 1. Udio
    init_udio_db(db_file)
    with udio_conn(db_file) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.upper() == "WAL"
    ok = save_udio_account_to_db("test_u@example.com", username="testu", db_path=db_file)
    assert ok is True

    # 2. ElevenLabs
    init_elevenlabs_db(db_file)
    with el_conn(db_file) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.upper() == "WAL"
    ok = save_elevenlabs_account_to_db("test_el@example.com", "pass123", db_path=db_file)
    assert ok is True

    # 3. Muse
    init_muse_db(db_file)
    with muse_conn(db_file) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.upper() == "WAL"
    ok = save_muse_account_to_db("test_m@example.com", {"hatch_sess": "s123"}, db_path=db_file)
    assert ok is True

    # 4. Stable Audio
    init_stableaudio_db(db_file)
    with sa_conn(db_file) as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        assert journal_mode.upper() == "WAL"
    ok = save_stableaudio_account_to_db("test_sa@example.com", "pass123", db_path=db_file)
    assert ok is True
