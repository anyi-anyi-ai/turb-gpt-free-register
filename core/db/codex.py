# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any

from .base import (
    _LOCK, _now, _sqlite_conn, _ensure_sqlite, _next_id,
)

# ============================================================
# Codex 授权账号（SQLite codex_accounts 表）
# ============================================================

def _codex_filter_sql(
    archived: str | bool | None = "0",
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
) -> tuple[list[str], list[Any]]:
    where: list[str] = []
    params: list[Any] = []
    if archived in (True, "1", "true", "yes", "only"):
        where.append("archived=1")
    elif archived not in ("all", "include"):
        where.append("archived=0")
    if date_from:
        value = str(date_from)
        where.append("created_at >= ?")
        params.append(value + ("T00:00:00" if len(value) == 10 else ""))
    if date_to:
        value = str(date_to)
        where.append("created_at <= ?")
        params.append(value + ("T23:59:59.999999" if len(value) == 10 else ""))
    if q and str(q).strip():
        where.append("lower(payload) LIKE ?")
        params.append("%" + str(q).strip().lower() + "%")
    return where, params


def _codex_content_to_record(content: dict) -> dict:
    """把 SQLite 中的 Codex payload 转成列表展示对象。"""
    fname = content.get("_filename", "")
    without_prefix = fname[5:-5] if fname.startswith("codex-") and fname.endswith(".json") else fname
    email = content.get("email") or without_prefix
    plan = ""
    if "-" in without_prefix and without_prefix.rsplit("-", 1)[-1].lower() in ("free", "plus", "team", "pro", "enterprise"):
        plan = without_prefix.rsplit("-", 1)[-1].lower()
        if not content.get("email"):
            email = without_prefix.rsplit("-", 1)[0]
    return {
        "filename": fname, "path": f"sqlite://codex_accounts/{fname}", "email": email, "plan": plan,
        "account_id": content.get("account_id", ""), "type": content.get("type", "codex"),
        "last_refresh": content.get("last_refresh", ""), "expired": content.get("expired", ""),
        "access_token_preview": (content.get("access_token", "") or "")[:32],
        "size": content.get("_size", 0), "mtime": content.get("_mtime", ""),
        "exported_at": content.get("_exported_at"), "exported_count": content.get("_exported_count", 0),
        "archived": bool(content.get("_archived")), "archived_at": content.get("_archived_at"),
    }


def list_codex_accounts_page(
    archived: str | bool | None = "0",
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """直接在 codex_accounts 表执行分页查询，不读取 codex_accounts/ 文件。"""
    _ensure_sqlite()
    limit = max(1, int(limit))
    offset = max(0, int(offset or 0))
    where, params = _codex_filter_sql(archived, date_from, date_to, q)
    clause = " AND ".join(where) if where else "1=1"
    with closing(_sqlite_conn()) as conn:
        total = int(conn.execute(f"SELECT COUNT(*) FROM codex_accounts WHERE {clause}", params).fetchone()[0])
        latest = str(conn.execute(
            f"SELECT COALESCE(MAX(updated_at), '') FROM codex_accounts WHERE {clause}", params
        ).fetchone()[0] or "")
        rows = conn.execute(
            f"SELECT payload FROM codex_accounts WHERE {clause} "
            "ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
    items = [_codex_content_to_record(json.loads(row["payload"])) for row in rows]
    return {
        "items": items,
        "total": total,
        "offset": offset,
        "limit": limit,
        "revision": f"{total}:{latest}",
    }


def list_codex_accounts(
    archived: str | bool | None = "0",
    date_from: str | None = None,
    date_to: str | None = None,
    q: str | None = None,
) -> list[dict]:
    """从 SQLite 读取 Codex 凭证元数据，不扫描 codex_accounts/ 文件。"""
    _ensure_sqlite()
    where, params = _codex_filter_sql(archived, date_from, date_to, q)
    clause = " AND ".join(where) if where else "1=1"
    with closing(_sqlite_conn()) as conn:
        rows = [json.loads(row["payload"]) for row in conn.execute(
            f"SELECT payload FROM codex_accounts WHERE {clause} ORDER BY created_at DESC, id DESC", params
        )]
    return [_codex_content_to_record(content) for content in rows]


def upsert_codex_credential(content: dict, filename: str) -> str:
    """把 Codex 凭证写入 SQLite，返回逻辑文件名（不创建本地文件）。"""
    if not isinstance(content, dict) or not filename:
        raise ValueError("Codex 凭证或文件名无效")
    _ensure_sqlite()
    now = _now()
    with _LOCK, closing(_sqlite_conn()) as conn:
        old = conn.execute("SELECT payload, created_at FROM codex_accounts WHERE filename=?", (filename,)).fetchone()
        meta = dict(content)
        if old:
            previous = json.loads(old["payload"])
            for key in ("_exported_at", "_exported_count", "_archived", "_archived_at"):
                if key not in meta:
                    meta[key] = previous.get(key)
            created_at = old["created_at"] or now
            account_id = conn.execute("SELECT id FROM codex_accounts WHERE filename=?", (filename,)).fetchone()[0]
        else:
            account_id = int(conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM codex_accounts").fetchone()[0])
            created_at = now
        meta.update({"_filename": filename, "_size": len(json.dumps(content, ensure_ascii=False).encode("utf-8")), "_mtime": now})
        conn.execute(
            "INSERT INTO codex_accounts(id,filename,email,archived,created_at,updated_at,payload) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(filename) DO UPDATE SET email=excluded.email, archived=excluded.archived, updated_at=excluded.updated_at, payload=excluded.payload",
            (account_id, filename, str(content.get("email") or ""), int(bool(meta.get("_archived"))), created_at, now, json.dumps(meta, ensure_ascii=False)),
        )
        conn.commit()
    return filename

def archive_codex(filename: str, archived: bool = True) -> dict | None:
    """归档/取消归档一条 Codex 授权凭证。"""
    with _LOCK:
        if not filename.startswith("codex-") or not filename.endswith(".json"):
            raise ValueError(f"非法文件名: {filename}")
        if "/" in filename or "\\" in filename or ".." in filename:
            raise ValueError(f"非法文件名: {filename}")
        _ensure_sqlite()
        with closing(_sqlite_conn()) as conn:
            row = conn.execute("SELECT payload FROM codex_accounts WHERE filename=?", (filename,)).fetchone()
            if not row:
                return None
            content = json.loads(row["payload"])
            rec = {"exported_at": content.get("_exported_at"), "exported_count": content.get("_exported_count", 0)}
        rec["archived"] = bool(archived)
        rec["archived_at"] = _now() if archived else None
        content.update({"_archived": rec["archived"], "_archived_at": rec["archived_at"]})
        with closing(_sqlite_conn()) as conn:
            conn.execute("UPDATE codex_accounts SET archived=?, updated_at=?, payload=? WHERE filename=?", (int(archived), _now(), json.dumps(content, ensure_ascii=False), filename))
            conn.commit()
        return rec


def read_codex_credential(filename: str) -> tuple[str, str]:
    """
    读取一个 codex-*.json 文件原始内容。
    Returns: (content_string, filename)
    抛 ValueError：文件名不合法（防目录穿越）/ 不存在。
    """
    with _LOCK:
        # 防注入：只允许 codex-*.json 模式，不允许路径分隔符
        if not filename.startswith("codex-") or not filename.endswith(".json"):
            raise ValueError(f"非法文件名: {filename}")
        if "/" in filename or "\\" in filename or ".." in filename:
            raise ValueError(f"非法文件名: {filename}")
        _ensure_sqlite()
        with closing(_sqlite_conn()) as conn:
            row = conn.execute("SELECT payload FROM codex_accounts WHERE filename=?", (filename,)).fetchone()
        if not row:
            raise ValueError(f"文件不存在: {filename}")
        content = json.loads(row["payload"])
        content = {k: v for k, v in content.items() if not k.startswith("_")}
        return json.dumps(content, ensure_ascii=False, indent=2), filename


def mark_codex_exported(filename: str) -> dict:
    """
    标记某个 codex 凭证已导出（导出计数 +1，记录最近导出时间）。
    Returns: 该 filename 当前的导出状态记录。
    """
    with _LOCK:
        _ensure_sqlite()
        with closing(_sqlite_conn()) as conn:
            row = conn.execute("SELECT payload FROM codex_accounts WHERE filename=?", (filename,)).fetchone()
            if not row:
                return {"exported_count": 0}
            content = json.loads(row["payload"])
        rec = {"exported_count": int(content.get("_exported_count", 0) or 0)}
        rec["exported_count"] = int(rec.get("exported_count", 0)) + 1
        rec["exported_at"] = _now()
        content.update({"_exported_count": rec["exported_count"], "_exported_at": rec["exported_at"]})
        with closing(_sqlite_conn()) as conn:
            conn.execute("UPDATE codex_accounts SET updated_at=?, payload=? WHERE filename=?", (_now(), json.dumps(content, ensure_ascii=False), filename))
            conn.commit()
        return rec


def reset_codex_exported(filename: str) -> None:
    """清掉某个 codex 凭证的导出状态（用户想重置时用）。"""
    with _LOCK:
        _ensure_sqlite()
        with closing(_sqlite_conn()) as conn:
            row = conn.execute("SELECT payload FROM codex_accounts WHERE filename=?", (filename,)).fetchone()
            if not row:
                return
            content = json.loads(row["payload"])
            content.update({"_exported_count": 0, "_exported_at": None})
            conn.execute("UPDATE codex_accounts SET updated_at=?, payload=? WHERE filename=?", (_now(), json.dumps(content, ensure_ascii=False), filename))
            conn.commit()


def delete_codex_credential(filename: str) -> bool:
    """从 SQLite 删除一个 Codex 凭证。"""
    with _LOCK:
        if not filename.startswith("codex-") or not filename.endswith(".json"):
            raise ValueError(f"非法文件名: {filename}")
        if "/" in filename or "\\" in filename or ".." in filename:
            raise ValueError(f"非法文件名: {filename}")
        _ensure_sqlite()
        with closing(_sqlite_conn()) as conn:
            cur = conn.execute("DELETE FROM codex_accounts WHERE filename=?", (filename,))
            conn.commit()
            return cur.rowcount > 0


def codex_accounts_summary() -> dict:
    """codex 账号汇总：总数 / 已导出 / 未导出。"""
    with _LOCK:
        _ensure_sqlite()
        with closing(_sqlite_conn()) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS total, "
                "SUM(CASE WHEN COALESCE(CAST(json_extract(payload, '$._exported_count') AS INTEGER), 0) > 0 "
                "THEN 1 ELSE 0 END) AS exported "
                "FROM codex_accounts WHERE archived=0"
            ).fetchone()
        total = int(row["total"] or 0)
        exported = int(row["exported"] or 0)
        return {
            "total": total,
            "exported": exported,
            "pending": total - exported,
        }


