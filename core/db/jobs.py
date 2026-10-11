# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

from .base import (
    _LOCK, _now, _sqlite_conn, _ensure_sqlite, _load_collection, _save_collection,
    _query_collection, _query_collection_page, _next_id, _LOG_DIR,
)

def _load_jobs() -> list[dict]:
    return _load_collection("jobs")


def _save_jobs(rows: list[dict]) -> None:
    _save_collection("jobs", rows)



# ============================================================
# registration_jobs
# ============================================================

def _new_job_row(
    rows: list[dict],
    *,
    job_type: str = "registration",
    email_source: str,
    country: str | None = None,
    parent_job_id: int | None = None,
    root_job_id: int | None = None,
    retry_attempt: int = 0,
    retry_action: str | None = None,
    email: str | None = None,
    account_id: int | None = None,
) -> dict:
    job_uuid = str(uuid.uuid4())
    log_file = str(_LOG_DIR / f"{job_uuid}.log")
    Path(log_file).parent.mkdir(parents=True, exist_ok=True)
    return {
        "id": _next_id(rows),
        "job_uuid": job_uuid,
        "job_type": job_type,
        "country": str(country).strip().upper() if country else None,
        "parent_job_id": parent_job_id,
        "root_job_id": root_job_id,
        "retry_attempt": int(retry_attempt or 0),
        "retry_action": retry_action,
        "email_source": email_source,
        "email": email,
        "status": "pending",
        "error_message": None,
        "log_file": log_file,
        "started_at": None,
        "completed_at": None,
        "account_id": account_id,
        "network_traffic": None,
        "created_at": _now(),
    }


def create_job(email_source: str, *, country: str | None = None) -> dict:
    """创建一个首次执行的 pending 注册任务。"""
    _ensure_sqlite()
    with _LOCK:
        with closing(_sqlite_conn()) as conn:
            with conn:
                max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM registration_jobs").fetchone()[0]
                new_id = int(max_id) + 1
                job_uuid = str(uuid.uuid4())
                log_file = str(_LOG_DIR / f"{job_uuid}.log")
                Path(log_file).parent.mkdir(parents=True, exist_ok=True)
                now_str = _now()
                row = {
                    "id": new_id,
                    "job_uuid": job_uuid,
                    "job_type": "registration",
                    "country": str(country).strip().upper() if country else None,
                    "parent_job_id": None,
                    "root_job_id": new_id,
                    "retry_attempt": 0,
                    "retry_action": None,
                    "email_source": email_source,
                    "email": None,
                    "status": "pending",
                    "error_message": None,
                    "log_file": log_file,
                    "started_at": None,
                    "completed_at": None,
                    "account_id": None,
                    "network_traffic": None,
                    "created_at": now_str,
                }
                conn.execute(
                    "INSERT INTO registration_jobs(id, email, status, archived, created_at, updated_at, payload) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (new_id, "", "pending", 0, now_str, "", json.dumps(row, ensure_ascii=False)),
                )
                return dict(row)


def create_retry_job(
    source_job_id: int,
    *,
    job_type: str,
    email_source: str,
    email: str | None = None,
    account_id: int | None = None,
) -> tuple[dict, bool]:
    """原子创建重试子任务；同一任务链已有活跃任务时直接复用。"""
    with _LOCK:
        rows = _load_jobs()
        source = next((r for r in rows if int(r.get("id") or 0) == int(source_job_id)), None)
        if source is None:
            raise LookupError("任务不存在")
        if source.get("status") not in ("failed", "stopped", "cancelled"):
            raise ValueError(f"当前状态不支持重试：{source.get('status')}")

        root_id = int(source.get("root_job_id") or source.get("id"))
        active_states = {"pending", "running", "stopping"}
        active = next((
            r for r in rows
            if int(r.get("id") or 0) != int(source_job_id)
            and int(r.get("root_job_id") or 0) == root_id
            and r.get("status") in active_states
        ), None)
        if active is not None:
            if active.get("job_type", "registration") != job_type:
                raise ValueError(f"已有其他类型重试任务 #{active.get('id')} 在排队或运行中")
            return dict(active), False

        attempts = [
            int(r.get("retry_attempt") or 0)
            for r in rows
            if int(r.get("id") or 0) == root_id or int(r.get("root_job_id") or 0) == root_id
        ]
        row = _new_job_row(
            rows,
            email_source=email_source,
            job_type=job_type,
            parent_job_id=int(source_job_id),
            root_job_id=root_id,
            retry_attempt=(max(attempts) if attempts else 0) + 1,
            retry_action=("codex" if job_type == "codex_retry" else "registration"),
            email=email,
            account_id=account_id,
        )
        rows.append(row)
        _save_jobs(rows)
        return dict(row), True


def update_job(
    job_id: int,
    *,
    status: str | None = None,
    email: str | None = None,
    error: str | None = None,
    started_at: str | None = None,
    completed_at: str | None = None,
    account_id: int | None = None,
    network_traffic: dict | None = None,
) -> None:
    _ensure_sqlite()
    with _LOCK:
        with closing(_sqlite_conn()) as conn:
            with conn:
                row_db = conn.execute(
                    "SELECT payload FROM registration_jobs WHERE id=?", (int(job_id),)
                ).fetchone()
                if not row_db:
                    return
                row = json.loads(row_db["payload"])
                if status is not None:
                    row["status"] = status
                if email is not None:
                    row["email"] = email
                if error is not None:
                    row["error_message"] = error
                if started_at is not None:
                    row["started_at"] = started_at
                if completed_at is not None:
                    row["completed_at"] = completed_at
                if account_id is not None:
                    row["account_id"] = account_id
                if network_traffic is not None:
                    row["network_traffic"] = dict(network_traffic)
                now_str = _now()
                row["updated_at"] = now_str
                conn.execute(
                    "UPDATE registration_jobs SET email=?, status=?, updated_at=?, payload=? WHERE id=?",
                    (
                        str(row.get("email") or ""),
                        str(row.get("status") or ""),
                        now_str,
                        json.dumps(row, ensure_ascii=False),
                        int(job_id),
                    ),
                )


def list_jobs(limit: int = 100) -> list[dict]:
    with _LOCK:
        return [dict(r) for r in _query_collection("jobs", limit=limit)]


def list_jobs_page(limit: int = 50, offset: int = 0) -> dict:
    """直接使用 registration_jobs 的 SQL LIMIT/OFFSET 返回任务页。"""
    with _LOCK:
        limit = max(1, int(limit))
        offset = max(0, int(offset or 0))
        rows, total, latest = _query_collection_page(
            "jobs", limit=limit, offset=offset
        )
        return {
            "items": rows,
            "total": total,
            "offset": offset,
            "limit": limit,
            "revision": f"{total}:{latest}",
        }


def job_status_counts() -> dict:
    """在 SQLite 中聚合任务状态，避免为统计目的加载全部任务 payload。"""
    _ensure_sqlite()
    with closing(_sqlite_conn()) as conn:
        counts = {
            str(row["status"] or "unknown"): int(row["n"])
            for row in conn.execute(
                "SELECT status, COUNT(*) AS n FROM registration_jobs GROUP BY status"
            )
        }
    counts["active"] = sum(int(counts.get(status, 0) or 0) for status in ("pending", "running", "stopping"))
    return counts


def get_job(job_id: int) -> dict | None:
    _ensure_sqlite()
    with _LOCK:
        with closing(_sqlite_conn()) as conn:
            row = conn.execute(
                "SELECT payload FROM registration_jobs WHERE id=?", (int(job_id),)
            ).fetchone()
            return json.loads(row["payload"]) if row else None


def get_successful_retry_for_job(job_id: int) -> dict | None:
    """返回同一任务链中已成功的其他重试任务，用于保留原任务历史状态并阻止重复重试。"""
    with _LOCK:
        rows = _load_jobs()
        source = next((r for r in rows if int(r.get("id") or 0) == int(job_id)), None)
        if source is None:
            return None
        root_id = int(source.get("root_job_id") or source.get("id") or 0)
        matches = [
            r for r in rows
            if int(r.get("id") or 0) != int(job_id)
            and int(r.get("root_job_id") or 0) == root_id
            and r.get("status") == "success"
        ]
        if not matches:
            return None
        return dict(max(matches, key=lambda r: int(r.get("id") or 0)))


def delete_job(job_id: int, *, delete_log: bool = True, allow_running: bool = False) -> bool:
    """
    删除一个注册任务记录；默认同时删除该任务日志文件。返回是否删除到记录。
    默认不删除 running 任务，避免后台线程仍在执行但前端记录消失。
    """
    _ensure_sqlite()
    log_file = None
    with _LOCK:
        with closing(_sqlite_conn()) as conn:
            with conn:
                row_db = conn.execute(
                    "SELECT payload FROM registration_jobs WHERE id=?", (int(job_id),)
                ).fetchone()
                if not row_db:
                    return False
                row = json.loads(row_db["payload"])
                if not allow_running and row.get("status") in ("running", "stopping"):
                    return False
                conn.execute("DELETE FROM registration_jobs WHERE id=?", (int(job_id),))
                log_file = row.get("log_file")

    if delete_log and log_file:
        try:
            Path(log_file).unlink(missing_ok=True)
        except Exception:
            pass
    return True


