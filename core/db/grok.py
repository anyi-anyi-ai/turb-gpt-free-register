# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from typing import Any

from .base import _LOCK, _now, _sqlite_conn, _ensure_sqlite


def insert_grok_account(
    *,
    email: str,
    password: str,
    sso_token: str,
    sso_rw: str = "",
    refresh_token: str = "",
    access_token: str = "",
    token_expires_at: str = "",
    cpa_record: str = "",
    grokiq_synced: int = 0,
    status: str = "healthy",
    bot_flag_source: int | None = None,
    bot_flag_details: str = "",
    probe_tps: float = 0.0,
    probe_reasoning_tokens: int = 0,
    proxy_used: str = "",
    extra: dict | None = None,
) -> int:
    """插入或更新 Grok 账号记录。"""
    _ensure_sqlite()
    now_str = _now()
    payload = {
        "email": email,
        "password": password,
        "sso_token": sso_token,
        "sso_rw": sso_rw,
        "refresh_token": refresh_token,
        "access_token": access_token,
        "token_expires_at": token_expires_at,
        "cpa_record": cpa_record,
        "grokiq_synced": grokiq_synced,
        "status": status,
        "bot_flag_source": bot_flag_source,
        "bot_flag_details": bot_flag_details,
        "probe_tps": probe_tps,
        "probe_reasoning_tokens": probe_reasoning_tokens,
        "proxy_used": proxy_used,
        "extra": extra or {},
        "created_at": now_str,
        "updated_at": now_str,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)

    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            cur = conn.execute(
                "SELECT id, created_at, payload FROM grok_accounts WHERE email = ? COLLATE NOCASE LIMIT 1",
                (email,),
            )
            existing = cur.fetchone()
            if existing:
                row_id = existing["id"]
                created_at = existing["created_at"] or now_str
                payload["created_at"] = created_at
                payload_json = json.dumps(payload, ensure_ascii=False)
                conn.execute(
                    """
                    UPDATE grok_accounts SET
                        password = ?,
                        status = ?,
                        sso_token = ?,
                        sso_rw = ?,
                        refresh_token = ?,
                        access_token = ?,
                        token_expires_at = ?,
                        cpa_record = ?,
                        grokiq_synced = ?,
                        archived = 0,
                        bot_flag_source = ?,
                        bot_flag_details = ?,
                        probe_tps = ?,
                        probe_reasoning_tokens = ?,
                        updated_at = ?,
                        payload = ?
                    WHERE id = ?
                    """,
                    (
                        password,
                        status,
                        sso_token,
                        sso_rw,
                        refresh_token,
                        access_token,
                        token_expires_at,
                        cpa_record,
                        grokiq_synced,
                        bot_flag_source,
                        bot_flag_details,
                        probe_tps,
                        probe_reasoning_tokens,
                        now_str,
                        payload_json,
                        row_id,
                    ),
                )
                return int(row_id)
            else:
                cur = conn.execute(
                    """
                    INSERT INTO grok_accounts (
                        email, password, status, sso_token, sso_rw,
                        refresh_token, access_token, token_expires_at, cpa_record, grokiq_synced,
                        archived, bot_flag_source, bot_flag_details, probe_tps,
                        probe_reasoning_tokens, created_at, updated_at, payload
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        email,
                        password,
                        status,
                        sso_token,
                        sso_rw,
                        refresh_token,
                        access_token,
                        token_expires_at,
                        cpa_record,
                        grokiq_synced,
                        bot_flag_source,
                        bot_flag_details,
                        probe_tps,
                        probe_reasoning_tokens,
                        now_str,
                        now_str,
                        payload_json,
                    ),
                )
                return int(cur.lastrowid)


def get_grok_account_by_email(email: str) -> dict[str, Any] | None:
    _ensure_sqlite()
    with _LOCK, closing(_sqlite_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM grok_accounts WHERE email = ? COLLATE NOCASE AND archived = 0 LIMIT 1",
            (email,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["payload"] = json.loads(data["payload"]) if data.get("payload") else {}
        except Exception:
            data["payload"] = {}
        return data


def query_grok_accounts(
    *,
    status: str | None = None,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict[str, Any]:
    """分页查询 Grok 账号。"""
    _ensure_sqlite()
    conditions = ["archived = 0"]
    params: list[Any] = []

    if status and status.strip():
        conditions.append("status = ?")
        params.append(status.strip())

    if search and search.strip():
        conditions.append("(email LIKE ? OR password LIKE ? OR bot_flag_details LIKE ?)")
        pattern = f"%{search.strip()}%"
        params.extend([pattern, pattern, pattern])

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""
    offset = max(0, (page - 1) * page_size)

    with _LOCK, closing(_sqlite_conn()) as conn:
        count_cur = conn.execute(f"SELECT COUNT(*) AS total FROM grok_accounts{where_clause}", params)
        total = int(count_cur.fetchone()["total"])

        query_sql = f"""
            SELECT id, email, password, status, sso_token, sso_rw,
                   bot_flag_source, bot_flag_details, probe_tps,
                   probe_reasoning_tokens, created_at, updated_at, payload
            FROM grok_accounts
            {where_clause}
            ORDER BY id DESC
            LIMIT ? OFFSET ?
        """
        rows = conn.execute(query_sql, [*params, page_size, offset]).fetchall()
        items = []
        for r in rows:
            item = dict(r)
            try:
                item["payload"] = json.loads(item["payload"]) if item.get("payload") else {}
            except Exception:
                item["payload"] = {}
            items.append(item)

        # 统计各状态计数
        stats_cur = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM grok_accounts WHERE archived = 0 GROUP BY status"
        )
        status_counts = {r["status"]: r["cnt"] for r in stats_cur.fetchall()}

        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "status_counts": status_counts,
        }


def delete_grok_accounts(account_ids: list[int] | None = None, emails: list[str] | None = None) -> int:
    """批量删除 Grok 账号（软删除 archived=1）。"""
    _ensure_sqlite()
    deleted = 0
    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            if account_ids:
                placeholders = ",".join("?" * len(account_ids))
                cur = conn.execute(
                    f"UPDATE grok_accounts SET archived = 1, updated_at = ? WHERE id IN ({placeholders})",
                    [_now(), *account_ids],
                )
                deleted += cur.rowcount
            if emails:
                placeholders = ",".join("?" * len(emails))
                cur = conn.execute(
                    f"UPDATE grok_accounts SET archived = 1, updated_at = ? WHERE email IN ({placeholders}) COLLATE NOCASE",
                    [_now(), *emails],
                )
                deleted += cur.rowcount
    return deleted


def export_grok_accounts_text(
    *,
    status: str | None = None,
    exclude_degraded: bool = True,
    format_type: str = "txt",  # "txt", "json", "cpa", "grok2api"
) -> str:
    """导出 Grok 账号凭证，支持过滤掉降智和风控账号。"""
    _ensure_sqlite()
    conditions = ["archived = 0"]
    params: list[Any] = []

    if status and status.strip():
        conditions.append("status = ?")
        params.append(status.strip())
    elif exclude_degraded:
        conditions.append("status NOT IN ('degraded', 'bot_flagged', 'disabled')")

    where_clause = " WHERE " + " AND ".join(conditions) if conditions else ""

    with _LOCK, closing(_sqlite_conn()) as conn:
        rows = conn.execute(
            f"SELECT id, email, password, sso_token, sso_rw, refresh_token, access_token, token_expires_at, cpa_record, status, bot_flag_source, probe_tps, created_at FROM grok_accounts{where_clause} ORDER BY id ASC",
            params,
        ).fetchall()

        if format_type == "json":
            export_list = [dict(r) for r in rows]
            return json.dumps(export_list, ensure_ascii=False, indent=2)

        if format_type == "cpa":
            cpa_list = []
            for r in rows:
                cpa_raw = str(r["cpa_record"] or "").strip()
                if cpa_raw:
                    try:
                        cpa_list.append(json.loads(cpa_raw))
                        continue
                    except Exception:
                        pass
                # 兼容从已有字段动态生成
                from core.grok.auth_exchange import token_to_cpa_record
                rec = token_to_cpa_record(
                    token={
                        "access_token": r["access_token"],
                        "refresh_token": r["refresh_token"],
                    },
                    email=r["email"],
                    sso=r["sso_token"],
                )
                cpa_list.append(rec)
            return json.dumps(cpa_list, ensure_ascii=False, indent=2)

        if format_type == "grok2api":
            grok2api_list = []
            for r in rows:
                grok2api_list.append({
                    "email": r["email"],
                    "access_token": r["access_token"] or r["sso_token"],
                    "refresh_token": r["refresh_token"],
                    "sso": r["sso_token"],
                    "password": r["password"],
                    "status": r["status"],
                })
            return json.dumps(grok2api_list, ensure_ascii=False, indent=2)

        # 默认导出为 gptGrok2api 兼容文本格式: email----password----sso_token
        lines = []
        for r in rows:
            token = str(r["sso_token"]).strip()
            if not token.startswith("sso="):
                token = f"sso={token}"
            lines.append(f"{r['email']}----{r['password']}----{token}")
        return "\n".join(lines)


# ----------------------------------------------------------------------
# GrokIQ Outbox 事件队列持久化操作
# ----------------------------------------------------------------------

def enqueue_grokiq_outbox(
    *,
    event_id: str,
    account_id: int = 0,
    email: str = "",
    payload: dict[str, Any] | None = None,
) -> int:
    """写入待投递至 GrokIQ Webhook 的 Outbox 记录。"""
    _ensure_sqlite()
    now_str = _now()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)

    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO grokiq_outbox (
                    event_id, account_id, email, payload, status,
                    retry_count, next_retry_at, last_error, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', 0, ?, '', ?, ?)
                """,
                (event_id, account_id, email, payload_json, time.time(), now_str, now_str),
            )
            return int(cur.lastrowid or 0)


def fetch_pending_outbox_events(limit: int = 20) -> list[dict[str, Any]]:
    """拉取到达重试时间且处于待投递/重试中的事件。"""
    _ensure_sqlite()
    now_ts = time.time()
    with _LOCK, closing(_sqlite_conn()) as conn:
        rows = conn.execute(
            """
            SELECT * FROM grokiq_outbox
            WHERE status IN ('pending', 'retrying') AND next_retry_at <= ?
            ORDER BY id ASC LIMIT ?
            """,
            (now_ts, limit),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"]) if d.get("payload") else {}
            except Exception:
                d["payload"] = {}
            result.append(d)
        return result


def update_outbox_status(
    event_id: str,
    *,
    status: str,
    retry_count: int,
    next_retry_at: float,
    last_error: str = "",
) -> None:
    """更新 Outbox 投递状态。"""
    _ensure_sqlite()
    now_str = _now()
    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            conn.execute(
                """
                UPDATE grokiq_outbox SET
                    status = ?,
                    retry_count = ?,
                    next_retry_at = ?,
                    last_error = ?,
                    updated_at = ?
                WHERE event_id = ?
                """,
                (status, retry_count, next_retry_at, last_error, now_str, event_id),
            )


def mark_account_grokiq_synced(account_id: int) -> None:
    """标记账号已成功通过 Webhook 同步到 GrokIQ。"""
    _ensure_sqlite()
    now_str = _now()
    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            conn.execute(
                "UPDATE grok_accounts SET grokiq_synced = 1, updated_at = ? WHERE id = ?",
                (now_str, account_id),
            )


def update_grok_account_status(
    account_id: int | str,
    *args,
    status: str | None = None,
    bot_flag_source: int | None = None,
    bot_flag_details: str = "",
    probe_tps: float = 0.0,
    probe_reasoning_tokens: int = 0,
    remark: str = "",
    **kwargs,
) -> bool:
    """更新 Grok 账号的状态、Bot 标记与探针测量数据。
    向后兼容：当 account_id 为 str 时，自动作为 email 转发至 update_grok_account_status_by_email。
    """
    if isinstance(account_id, str):
        st = status or (args[0] if args else "unknown")
        rmk = remark or bot_flag_details or (args[1] if len(args) > 1 else "")
        return update_grok_account_status_by_email(account_id, st, rmk)

    if status is None:
        status = args[0] if args else "unknown"

    _ensure_sqlite()
    now_str = _now()
    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            cur = conn.execute(
                """
                UPDATE grok_accounts SET
                    status = ?,
                    bot_flag_source = COALESCE(?, bot_flag_source),
                    bot_flag_details = CASE WHEN ? != '' THEN ? ELSE bot_flag_details END,
                    probe_tps = CASE WHEN ? > 0 THEN ? ELSE probe_tps END,
                    probe_reasoning_tokens = CASE WHEN ? > 0 THEN ? ELSE probe_reasoning_tokens END,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    bot_flag_source,
                    bot_flag_details,
                    bot_flag_details,
                    probe_tps,
                    probe_tps,
                    probe_reasoning_tokens,
                    probe_reasoning_tokens,
                    now_str,
                    account_id,
                ),
            )
            return cur.rowcount > 0


def get_grok_accounts_by_ids(account_ids: list[int]) -> list[dict[str, Any]]:
    """根据 ID 列表批量查询 Grok 账号记录。"""
    if not account_ids:
        return []
    _ensure_sqlite()
    placeholders = ",".join("?" for _ in account_ids)
    with _LOCK, closing(_sqlite_conn()) as conn:
        rows = conn.execute(
            f"SELECT * FROM grok_accounts WHERE id IN ({placeholders}) ORDER BY id ASC",
            tuple(account_ids),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"]) if d.get("payload") else {}
            except Exception:
                d["payload"] = {}
            result.append(d)
        return result


def get_unsynced_grok_accounts(limit: int = 50) -> list[dict[str, Any]]:
    """获取所有尚未同步到 gptGrok2api 的有效 Grok 账号。"""
    _ensure_sqlite()
    with _LOCK, closing(_sqlite_conn()) as conn:
        rows = conn.execute(
            """
            SELECT * FROM grok_accounts
            WHERE archived = 0 AND status = 'healthy' AND grokiq_synced = 0
            ORDER BY id ASC LIMIT ?
            """,
            (limit,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"]) if d.get("payload") else {}
            except Exception:
                d["payload"] = {}
            result.append(d)
        return result


def mark_grok_accounts_synced(account_ids: list[int]) -> None:
    """批量标记 Grok 账号已成功同步到 gptGrok2api / GrokIQ。"""
    if not account_ids:
        return
    _ensure_sqlite()
    now_str = _now()
    placeholders = ",".join("?" for _ in account_ids)
    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            conn.execute(
                f"UPDATE grok_accounts SET grokiq_synced = 1, updated_at = ? WHERE id IN ({placeholders})",
                (now_str, *account_ids),
            )


def update_grok_account_status_by_email(email: str, status: str, remark: str = "") -> bool:
    """更新指定 Grok 账号的状态与备注。"""
    _ensure_sqlite()
    now_str = _now()
    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            cur = conn.execute(
                "SELECT id, payload FROM grok_accounts WHERE email = ? COLLATE NOCASE LIMIT 1",
                (email,),
            )
            row = cur.fetchone()
            if not row:
                return False
            payload = {}
            try:
                payload = json.loads(row["payload"]) if row["payload"] else {}
            except Exception:
                pass
            payload["status"] = status
            if remark:
                payload["remark"] = remark
            payload["updated_at"] = now_str
            conn.execute(
                """
                UPDATE grok_accounts SET
                    status = ?,
                    bot_flag_details = CASE WHEN ? != '' THEN ? ELSE bot_flag_details END,
                    updated_at = ?,
                    payload = ?
                WHERE id = ?
                """,
                (status, remark, remark, now_str, json.dumps(payload, ensure_ascii=False), row["id"]),
            )
            return True


