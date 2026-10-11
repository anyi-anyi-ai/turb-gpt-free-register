# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime
from typing import Any

from .base import (
    _LOCK, _now, _sqlite_conn, _ensure_sqlite, _load_collection, _save_collection,
    _query_collection, _query_collection_page, _pool_summary_sql, _find_by_email,
    _next_id, _outlook_line, _generic_api_email_line, _imap_email_line,
    _TABLES, _EMAIL_SOURCES, _DOMAIN_EMAIL_JSON,
    list_email_pool_page, _decorate_outlook, _decorate_generic_api_email, _decorate_imap_email,
    _account_line,
)
from .accounts import _load_accounts, _save_accounts

def _load_outlook() -> list[dict]:
    return _load_collection("outlook")


def _save_outlook(rows: list[dict]) -> None:
    _save_collection("outlook", rows)


def _load_generic_api_emails() -> list[dict]:
    return _load_collection("generic_api")


def _save_generic_api_emails(rows: list[dict]) -> None:
    for row in rows:
        row["copy_line"] = _generic_api_email_line(row)
    _save_collection("generic_api", rows)


def _load_imap_emails() -> list[dict]:
    return _load_collection("imap")


def _save_imap_emails(rows: list[dict]) -> None:
    for row in rows:
        row["copy_line"] = _imap_email_line(row)
    _save_collection("imap", rows)


def _load_uned_emails() -> list[dict]:
    return _load_collection("uned")


def _save_uned_emails(rows: list[dict]) -> None:
    from .base import _uned_line
    for row in rows:
        row["copy_line"] = _uned_line(row)
    _save_collection("uned", rows)


def uned_email_pool_summary(for_service: str = "chatgpt") -> dict:
    return _pool_summary_sql("uned")


def sync_uned_accounts_to_email_pool() -> dict:
    """把 uned_accounts 中的学生邮箱全量同步/映射至 email_pool (source='uned')。"""
    from .base import _uned_line, _next_id
    _ensure_sqlite()
    synced = 0
    updated = 0
    with _LOCK, closing(_sqlite_conn()) as conn:
        with conn:
            cur = conn.cursor()
            cur.execute("SELECT LOWER(email) FROM accounts")
            registered_emails = {r[0] for r in cur.fetchall()}

            cur.execute("""
                SELECT id, uned_identifier, email, full_name, birth_date, doc_number, phone, payload_json, created_at
                FROM uned_accounts
                ORDER BY id ASC
            """)
            uned_rows = cur.fetchall()

            for r in uned_rows:
                uned_id = r["id"]
                ident = str(r["uned_identifier"] or "").strip().lower()
                forward_outlook = str(r["email"] or "").strip()
                full_name = str(r["full_name"] or "").strip()
                birth_date = str(r["birth_date"] or "").strip()
                doc_number = str(r["doc_number"] or "").strip()
                phone = str(r["phone"] or "").strip()
                created_at = str(r["created_at"] or _now()).strip()

                payload_json = {}
                if r["payload_json"]:
                    try:
                        payload_json = json.loads(r["payload_json"])
                    except Exception:
                        payload_json = {}
                extra = payload_json.get("extra") or {}
                password = str(extra.get("password") or "").strip()
                student_email = f"{ident}@alumno.uned.es".lower()

                is_used = student_email in registered_emails
                status = "used" if is_used else "available"

                row_payload = {
                    "id": uned_id,
                    "email": student_email,
                    "uned_identifier": ident,
                    "password": password,
                    "forward_outlook": forward_outlook,
                    "full_name": full_name,
                    "birth_date": birth_date,
                    "doc_number": doc_number,
                    "phone": phone,
                    "status": status,
                    "imported_at": created_at,
                    "source": "uned",
                    "note": "UNED学生邮箱(已配置转发)" if not is_used else "已注册ChatGPT",
                }
                row_payload["copy_line"] = _uned_line(row_payload)

                cur.execute("SELECT id, payload FROM email_pool WHERE source='uned' AND LOWER(email)=? LIMIT 1", (student_email,))
                existing = cur.fetchone()
                if existing:
                    conn.execute(
                        "UPDATE email_pool SET status=?, updated_at=?, payload=? WHERE id=?",
                        (status, _now(), json.dumps(row_payload, ensure_ascii=False), existing["id"])
                    )
                    updated += 1
                else:
                    cur.execute("SELECT COALESCE(MAX(id), 0) FROM email_pool")
                    new_id = int(cur.fetchone()[0]) + 1
                    row_payload["id"] = new_id
                    conn.execute(
                        "INSERT INTO email_pool (id, email, source, status, archived, created_at, updated_at, payload) "
                        "VALUES (?, ?, 'uned', ?, 0, ?, ?, ?)",
                        (new_id, student_email, status, created_at, _now(), json.dumps(row_payload, ensure_ascii=False))
                    )
                    synced += 1

    return {"ok": True, "synced": synced, "updated": updated, "total": len(uned_rows)}


def _claim_next_email_from_source(source_key: str, for_service: str = "chatgpt") -> dict | None:
    _ensure_sqlite()
    db_source = _EMAIL_SOURCES[source_key]
    with _LOCK:
        with closing(_sqlite_conn()) as conn:
            with conn:
                now_str = _now()
                if for_service in ("grok", "uned"):
                    target_table = "uned_accounts" if for_service == "uned" else "grok_accounts"
                    time_field = f"$.{for_service}_claimed_at"
                    cur = conn.execute(
                        f"""
                        UPDATE email_pool
                        SET updated_at=?,
                            payload=json_set(
                                CASE WHEN payload IS NULL OR payload='' THEN '{{}}' ELSE payload END,
                                '{time_field}', ?
                            )
                        WHERE id = (
                            SELECT id FROM email_pool
                            WHERE source=?
                              AND archived=0
                              AND status NOT IN ('failed', 'banned', 'disabled')
                              AND email NOT IN (SELECT email FROM {target_table} WHERE status != 'failed' COLLATE NOCASE)
                              AND (
                                  json_extract(payload, '{time_field}') IS NULL
                                  OR (strftime('%s', 'now', 'localtime') - strftime('%s', json_extract(payload, '{time_field}'))) > 300
                              )
                            ORDER BY
                              CASE WHEN status='used' THEN 0 ELSE 1 END,
                              id ASC
                            LIMIT 1
                        )
                        RETURNING id, payload
                        """,
                        (now_str, now_str, db_source),
                    )
                    row_db = cur.fetchone()
                    if not row_db:
                        return None
                    try:
                        payload = json.loads(row_db["payload"]) if row_db["payload"] else {}
                    except Exception:
                        payload = {}
                    payload[f"{for_service}_claimed_at"] = now_str
                    return payload
                else:
                    cur = conn.execute(
                        """
                        UPDATE email_pool
                        SET status='used',
                            updated_at=?,
                            payload=json_set(
                                CASE WHEN payload IS NULL OR payload='' THEN '{}' ELSE payload END,
                                '$.status', 'used',
                                '$.used_at', ?,
                                '$.note', json('null')
                            )
                        WHERE id = (
                            SELECT id FROM email_pool
                            WHERE source=? AND status='available' AND archived=0
                            ORDER BY id ASC
                            LIMIT 1
                        )
                        RETURNING id, payload
                        """,
                        (now_str, now_str, db_source),
                    )
                    row_db = cur.fetchone()
                    if not row_db:
                        return None
                    try:
                        payload = json.loads(row_db["payload"]) if row_db["payload"] else {}
                    except Exception:
                        payload = {}
                    payload["status"] = "used"
                    payload["used_at"] = now_str
                    payload["note"] = None
                    return payload


def _release_email_in_source(source_key: str, email: str, status: str = "available", note: str | None = None) -> None:
    _ensure_sqlite()
    db_source = _EMAIL_SOURCES[source_key]
    target = (email or "").strip()
    if not target:
        return
    with _LOCK:
        with closing(_sqlite_conn()) as conn:
            with conn:
                row_db = conn.execute(
                    "SELECT id, payload FROM email_pool WHERE source=? AND email=? COLLATE NOCASE LIMIT 1",
                    (db_source, target)
                ).fetchone()
                if not row_db:
                    return
                rid = row_db["id"]
                payload = json.loads(row_db["payload"])
                payload["status"] = status
                now_str = _now()
                if status == "available":
                    payload["used_at"] = None
                elif status in ("used", "failed", "disabled", "banned"):
                    payload["used_at"] = payload.get("used_at") or now_str
                if note is not None:
                    payload["note"] = note
                conn.execute(
                    "UPDATE email_pool SET status=?, updated_at=?, payload=? WHERE id=?",
                    (status, now_str, json.dumps(payload, ensure_ascii=False), rid)
                )


def _release_unconsumed_in_source(source_key: str, email: str, note: str | None = None) -> bool:
    _ensure_sqlite()
    db_source = _EMAIL_SOURCES[source_key]
    target = (email or "").strip()
    if not target:
        return False
    with _LOCK:
        with closing(_sqlite_conn()) as conn:
            with conn:
                if conn.execute("SELECT 1 FROM accounts WHERE email=? COLLATE NOCASE LIMIT 1", (target,)).fetchone():
                    return False
                row_db = conn.execute(
                    "SELECT id, payload FROM email_pool WHERE source=? AND email=? COLLATE NOCASE AND status='used' LIMIT 1",
                    (db_source, target)
                ).fetchone()
                if not row_db:
                    return False
                rid = row_db["id"]
                payload = json.loads(row_db["payload"])
                payload["status"] = "available"
                payload["used_at"] = None
                if note is not None:
                    payload["note"] = note
                now_str = _now()
                conn.execute(
                    "UPDATE email_pool SET status='available', updated_at=?, payload=? WHERE id=?",
                    (now_str, json.dumps(payload, ensure_ascii=False), rid)
                )
                return True


def _get_email_from_source(source_key: str, email: str) -> dict | None:
    _ensure_sqlite()
    db_source = _EMAIL_SOURCES[source_key]
    target = (email or "").strip()
    if not target:
        return None
    with _LOCK:
        with closing(_sqlite_conn()) as conn:
            row_db = conn.execute(
                "SELECT payload FROM email_pool WHERE source=? AND email=? COLLATE NOCASE LIMIT 1",
                (db_source, target)
            ).fetchone()
            if not row_db:
                return None
            return json.loads(row_db["payload"])




# ============================================================
# outlook_pool
# ============================================================

def import_outlook_accounts(records: list[dict]) -> tuple[int, int]:
    """
    批量导入 Outlook 账号。
    records 元素：{email, password, client_id, refresh_token}
    返回 (新增数, 跳过数)。
    """
    with _LOCK:
        rows = _load_outlook()
        inserted = skipped = 0
        for raw in records:
            email = (raw.get("email") or "").strip()
            if not email:
                skipped += 1
                continue
            if _find_by_email(rows, email):
                skipped += 1
                continue
            cid = (raw.get("client_id") or raw.get("clientId") or "").strip()
            rt = (raw.get("refresh_token") or raw.get("refreshToken") or "").strip()
            if (len(cid) > 100 and len(rt) <= 50) or (cid.startswith("M.") and not rt.startswith("M.")):
                cid, rt = rt, cid
            row = {
                "id": _next_id(rows),
                "email": email,
                "password": (raw.get("password") or "").strip(),
                "client_id": cid,
                "refresh_token": rt,
                "status": "available",
                "used_at": None,
                "note": None,
                "imported_at": _now(),
            }
            row["copy_line"] = _outlook_line(row)
            rows.append(row)
            inserted += 1
        _save_outlook(rows)
        return inserted, skipped


def import_uned_emails(records: list[dict]) -> tuple[int, int]:
    """
    批量导入 UNED 学生邮箱。
    records 元素：{email, password, forward_outlook, full_name, birth_date}
    返回 (新增数, 跳过数)。
    """
    from .base import _uned_line
    with _LOCK:
        rows = _load_uned_emails()
        inserted = skipped = 0
        for raw in records:
            email = (raw.get("email") or "").strip().lower()
            if not email:
                skipped += 1
                continue
            if _find_by_email(rows, email):
                skipped += 1
                continue
            row = {
                "id": _next_id(rows),
                "email": email,
                "uned_identifier": raw.get("uned_identifier") or email.split("@")[0],
                "password": (raw.get("password") or "").strip(),
                "forward_outlook": (raw.get("forward_outlook") or "").strip(),
                "full_name": (raw.get("full_name") or "").strip(),
                "birth_date": (raw.get("birth_date") or "").strip(),
                "status": raw.get("status") or "available",
                "used_at": raw.get("used_at") or None,
                "note": raw.get("note") or "UNED学生邮箱",
                "imported_at": raw.get("imported_at") or _now(),
                "source": "uned",
            }
            row["copy_line"] = _uned_line(row)
            rows.append(row)
            inserted += 1
        if inserted:
            _save_uned_emails(rows)
        return inserted, skipped


def import_registered_email_accounts(records: list[dict], source: str | None) -> tuple[int, int]:
    """
    把邮箱素材直接导入为“已注册成功账号”，用于跳过注册、直接在账号页补跑 Codex 授权。

    source:
      - outlook: records 元素 {email,password,client_id,refresh_token[,access_token,totp_secret]}
      - generic_api: records 元素 {email,code_url[,access_token,totp_secret]}
      - imap: records 元素 {email,imap_password,imap_server,imap_port,imap_ssl}

    返回 (新增账号数, 跳过数)。已存在账号会跳过；邮箱池中已存在的素材会复用并标记 used。
    """
    source = (source or "").strip().lower()
    if source not in ("outlook", "generic_api", "imap"):
        raise ValueError("source 必须显式传入 outlook / generic_api / imap")

    with _LOCK:
        accounts = _load_accounts()
        outlook_rows = _load_outlook()
        generic_rows = _load_generic_api_emails()
        imap_rows = _load_imap_emails()
        inserted = skipped = 0

        for raw in records:
            email = (raw.get("email") or "").strip()
            if not email:
                skipped += 1
                continue
            if _find_by_email(accounts, email):
                skipped += 1
                continue

            now = _now()
            original_line = email
            pool_row = None

            if source == "imap":
                password = str(raw.get("imap_password") or raw.get("password") or "").strip()
                server = str(raw.get("imap_server") or raw.get("server") or "").strip()
                try:
                    port = int(raw.get("imap_port") or raw.get("port") or 993)
                except (TypeError, ValueError):
                    port = 0
                if not password or not server or not (1 <= port <= 65535):
                    skipped += 1
                    continue
                ssl_raw = raw.get("imap_ssl", raw.get("use_ssl", True))
                use_ssl = ssl_raw if isinstance(ssl_raw, bool) else str(ssl_raw).strip().lower() not in {"0", "false", "no", "off"}
                pool_row = _find_by_email(imap_rows, email)
                values = {
                    "imap_password": password, "imap_server": server, "imap_port": port,
                    "imap_username": str(raw.get("imap_username") or raw.get("username") or "").strip(),
                    "imap_ssl": bool(use_ssl),
                }
                if pool_row is None:
                    pool_row = {"id": _next_id(imap_rows), "email": email, **values,
                                "status": "used", "used_at": now,
                                "note": "导入为已注册账号，用于 Codex 授权", "imported_at": now}
                    imap_rows.append(pool_row)
                else:
                    pool_row.update(values)
                pool_row["status"] = "used"
                pool_row["used_at"] = pool_row.get("used_at") or now
                pool_row["completed_at"] = pool_row.get("completed_at") or now
                pool_row["note"] = pool_row.get("note") or "导入为已注册账号，用于 Codex 授权"
                pool_row["copy_line"] = _imap_email_line(pool_row)
                original_line = _imap_email_line(pool_row)
            elif source == "generic_api":
                code_url = (raw.get("code_url") or raw.get("url") or "").strip()
                if not code_url:
                    skipped += 1
                    continue
                pool_row = _find_by_email(generic_rows, email)
                if pool_row is None:
                    pool_row = {
                        "id": _next_id(generic_rows),
                        "email": email,
                        "code_url": code_url,
                        "status": "used",
                        "used_at": now,
                        "note": "导入为已注册账号，用于 Codex 授权",
                        "imported_at": now,
                    }
                    generic_rows.append(pool_row)
                else:
                    pool_row["code_url"] = code_url or pool_row.get("code_url")
                pool_row["status"] = "used"
                pool_row["used_at"] = pool_row.get("used_at") or now
                pool_row["completed_at"] = pool_row.get("completed_at") or now
                pool_row["note"] = pool_row.get("note") or "导入为已注册账号，用于 Codex 授权"
                pool_row["copy_line"] = _generic_api_email_line(pool_row)
                original_line = _generic_api_email_line(pool_row)
            else:
                password = (raw.get("password") or "").strip()
                client_id = (raw.get("client_id") or raw.get("clientId") or "").strip()
                refresh_token = (raw.get("refresh_token") or raw.get("refreshToken") or "").strip()
                if not (password and client_id and refresh_token):
                    skipped += 1
                    continue
                pool_row = _find_by_email(outlook_rows, email)
                if pool_row is None:
                    pool_row = {
                        "id": _next_id(outlook_rows),
                        "email": email,
                        "password": password,
                        "client_id": client_id,
                        "refresh_token": refresh_token,
                        "status": "used",
                        "used_at": now,
                        "note": "导入为已注册账号，用于 Codex 授权",
                        "imported_at": now,
                    }
                    outlook_rows.append(pool_row)
                else:
                    pool_row["password"] = password or pool_row.get("password")
                    pool_row["client_id"] = client_id or pool_row.get("client_id")
                    pool_row["refresh_token"] = refresh_token or pool_row.get("refresh_token")
                pool_row["status"] = "used"
                pool_row["used_at"] = pool_row.get("used_at") or now
                pool_row["completed_at"] = pool_row.get("completed_at") or now
                pool_row["note"] = pool_row.get("note") or "导入为已注册账号，用于 Codex 授权"
                pool_row["copy_line"] = _outlook_line(pool_row)
                original_line = _outlook_line(pool_row)

            row_id = _next_id(accounts)
            access_token = (raw.get("access_token") or raw.get("token") or "").strip()
            totp_secret = (raw.get("totp_secret") or raw.get("totp") or "").strip() or None
            account = {
                "id": row_id,
                "email": email,
                "created_at": now,
                "access_token": access_token,
                "totp_secret": totp_secret,
                "user_id": raw.get("user_id"),
                "user_name": raw.get("user_name") or "Imported Account",
                "plan_type": raw.get("plan_type"),
                "expires_at": raw.get("expires_at"),
                "device_id": raw.get("device_id"),
                "proxy_used": raw.get("proxy_used"),
                "email_source": source,
                "extra_json": json.dumps({"imported_registered": True}, ensure_ascii=False),
                "codex_status": raw.get("codex_status") or "",
                "codex_error": raw.get("codex_error"),
                "updated_at": now,
                "original_email_line": original_line,
            }
            if source == "outlook":
                account["password"] = pool_row.get("password")
                account["client_id"] = pool_row.get("client_id")
                account["refresh_token"] = pool_row.get("refresh_token")
            account["copy_line"] = _account_line(account)
            accounts.append(account)

            pool_row["registered_account_id"] = row_id
            pool_row["access_token"] = access_token
            if totp_secret:
                pool_row["totp_secret"] = totp_secret
            inserted += 1

        _save_outlook(outlook_rows)
        _save_generic_api_emails(generic_rows)
        _save_imap_emails(imap_rows)
        _save_accounts(accounts)
        return inserted, skipped


def claim_next_outlook(for_service: str = "chatgpt") -> dict | None:
    """原子领取一个可用 Outlook 账号并标记。
    for_service='chatgpt': 严格要求 status='available'。
    for_service='grok': 复用未在 Grok 注册过的 Outlook 账号（支持 status='used' 的存量邮箱）。
    """
    row = _claim_next_email_from_source("outlook", for_service=for_service)
    return _decorate_outlook(row) if row else None


def release_outlook(email: str, status: str = "available", note: str | None = None) -> None:
    """把账号状态改回 available，或标记为 used/failed/disabled。"""
    _release_email_in_source("outlook", email, status=status, note=note)


def release_unconsumed_outlook(email: str, note: str | None = None) -> bool:
    """原子回收未生成本地账号且仍为 used 的 Outlook 邮箱。"""
    return _release_unconsumed_in_source("outlook", email, note=note)


def delete_email_pool(email: str, source: str = "all") -> bool:
    """按邮箱及来源从统一邮箱池删除记录。

    ``source=all`` 必须真正查询三个来源，而不能退化成 Outlook；旧版 WebUI
    在“全部邮箱池”中删除通用 API/域名邮箱时就是因此一直返回未找到。直接
    删除 SQLite 行也避免了逐条批量删除时反复加载并重写整个邮箱池。
    """
    target = str(email or "").strip()
    source = str(source or "all").strip().lower()
    if not target:
        return False
    if source not in {"all", "outlook", "generic_api", "imap", "cloudflare_domain"}:
        raise ValueError(f"非法邮箱来源: {source}")

    with _LOCK:
        _ensure_sqlite()
        with closing(_sqlite_conn()) as conn:
            if source == "all":
                cur = conn.execute(
                    "DELETE FROM email_pool WHERE email = ? COLLATE NOCASE",
                    (target,),
                )
            else:
                db_source = (
                    _EMAIL_SOURCES["domain"]
                    if source == "cloudflare_domain"
                    else _EMAIL_SOURCES[source]
                )
                cur = conn.execute(
                    "DELETE FROM email_pool "
                    "WHERE email = ? COLLATE NOCASE AND source = ?",
                    (target, db_source),
                )
            conn.commit()
            return cur.rowcount > 0


def delete_outlook(email: str) -> bool:
    """从邮箱池彻底删除一个邮箱（按 email 匹配）。返回是否删到。"""
    return delete_email_pool(email, source="outlook")


def list_outlook_pool(status: str | None = None, limit: int = 500) -> list[dict]:
    return list_email_pool_page(
        source="outlook", status=status, limit=limit, offset=0
    )["items"]


def outlook_pool_summary(for_service: str = "chatgpt") -> dict:
    with _LOCK:
        if for_service == "grok":
            with closing(_sqlite_conn()) as conn:
                cur = conn.cursor()
                total = cur.execute("SELECT COUNT(*) FROM email_pool WHERE source='outlook' AND archived=0").fetchone()[0]
                banned_failed = cur.execute(
                    "SELECT COUNT(*) FROM email_pool WHERE source='outlook' AND archived=0 AND status IN ('failed', 'banned', 'disabled')"
                ).fetchone()[0]
                registered_grok = cur.execute(
                    """
                    SELECT COUNT(*) FROM email_pool
                    WHERE source='outlook' AND archived=0
                      AND email IN (SELECT email FROM grok_accounts WHERE archived=0)
                    """
                ).fetchone()[0]
                available_for_grok = max(0, total - banned_failed - registered_grok)
                return {
                    "total": total,
                    "available_for_grok": available_for_grok,
                    "registered_on_grok": registered_grok,
                    "banned_or_failed": banned_failed,
                }
        out = _pool_summary_sql("outlook")
        out["total"] = sum(v for k, v in out.items() if k != "total")
        return out


def get_outlook_by_email(email: str) -> dict | None:
    row = _get_email_from_source("outlook", email)
    return _decorate_outlook(row) if row else None


# ============================================================
# generic_api email pool
# ============================================================

def import_generic_api_emails(records: list[dict]) -> tuple[int, int]:
    """
    批量导入通用 API 取码邮箱。
    records 元素：{email, code_url}
    返回 (新增数, 跳过数)。
    """
    with _LOCK:
        rows = _load_generic_api_emails()
        inserted = skipped = 0
        for raw in records:
            email = (raw.get("email") or "").strip()
            code_url = (raw.get("code_url") or raw.get("url") or "").strip()
            if not email or not code_url:
                skipped += 1
                continue
            if _find_by_email(rows, email):
                skipped += 1
                continue
            row = {
                "id": _next_id(rows),
                "email": email,
                "code_url": code_url,
                "status": "available",
                "used_at": None,
                "note": None,
                "imported_at": _now(),
            }
            row["copy_line"] = _generic_api_email_line(row)
            rows.append(row)
            inserted += 1
        _save_generic_api_emails(rows)
        return inserted, skipped


def claim_next_generic_api_email() -> dict | None:
    """原子领取一个可用通用 API 邮箱并标记为 used。"""
    row = _claim_next_email_from_source("generic_api")
    return _decorate_generic_api_email(row) if row else None


def release_generic_api_email(email: str, status: str = "available", note: str | None = None) -> None:
    """把通用 API 邮箱状态改回 available，或标记为 failed/used。"""
    _release_email_in_source("generic_api", email, status=status, note=note)


def release_unconsumed_generic_api_email(email: str, note: str | None = None) -> bool:
    """原子回收未生成本地账号且仍为 used 的通用 API 邮箱。"""
    return _release_unconsumed_in_source("generic_api", email, note=note)


def delete_generic_api_email(email: str) -> bool:
    """从通用 API 邮箱池彻底删除一个邮箱。"""
    return delete_email_pool(email, source="generic_api")


def list_generic_api_email_pool(status: str | None = None, limit: int = 500) -> list[dict]:
    return list_email_pool_page(
        source="generic_api", status=status, limit=limit, offset=0
    )["items"]


def generic_api_email_pool_summary() -> dict:
    with _LOCK:
        return _pool_summary_sql("generic_api")


def get_generic_api_email_by_email(email: str) -> dict | None:
    row = _get_email_from_source("generic_api", email)
    return _decorate_generic_api_email(row) if row else None


# ============================================================
# Generic IMAP email pool
# ============================================================

def import_imap_emails(records: list[dict]) -> tuple[int, int]:
    """导入 IMAP 邮箱。用户名留空时客户端使用邮箱地址登录。"""
    with _LOCK:
        rows = _load_imap_emails()
        inserted = skipped = 0
        for raw in records:
            email = str(raw.get("email") or "").strip()
            password = str(raw.get("imap_password") or raw.get("password") or "").strip()
            server = str(raw.get("imap_server") or raw.get("server") or "").strip()
            try:
                port = int(raw.get("imap_port") or raw.get("port") or 993)
            except (TypeError, ValueError):
                port = 0
            if not email or not password or not server or not (1 <= port <= 65535) or _find_by_email(rows, email):
                skipped += 1
                continue
            ssl_raw = raw.get("imap_ssl", raw.get("use_ssl", True))
            use_ssl = ssl_raw if isinstance(ssl_raw, bool) else str(ssl_raw).strip().lower() not in {"0", "false", "no", "off"}
            row = {
                "id": _next_id(rows), "email": email,
                "imap_password": password, "imap_server": server, "imap_port": port,
                "imap_username": str(raw.get("imap_username") or raw.get("username") or "").strip(),
                "imap_ssl": bool(use_ssl), "status": "available", "used_at": None,
                "note": None, "imported_at": _now(),
            }
            row["copy_line"] = _imap_email_line(row)
            rows.append(row)
            inserted += 1
        _save_imap_emails(rows)
        return inserted, skipped


def claim_next_imap_email() -> dict | None:
    row = _claim_next_email_from_source("imap")
    return _decorate_imap_email(row) if row else None


def release_imap_email(email: str, status: str = "available", note: str | None = None) -> None:
    _release_email_in_source("imap", email, status=status, note=note)


def release_unconsumed_imap_email(email: str, note: str | None = None) -> bool:
    return _release_unconsumed_in_source("imap", email, note=note)


def delete_imap_email(email: str) -> bool:
    return delete_email_pool(email, source="imap")


def list_imap_email_pool(status: str | None = None, limit: int = 500) -> list[dict]:
    return list_email_pool_page(source="imap", status=status, limit=limit, offset=0)["items"]


def imap_email_pool_summary() -> dict:
    with _LOCK:
        return _pool_summary_sql("imap")


def get_imap_email_by_email(email: str) -> dict | None:
    row = _get_email_from_source("imap", email)
    return _decorate_imap_email(row) if row else None




def _load_domain_pool() -> list[dict]:
    return _load_collection("domain")


def _save_domain_pool(rows: list[dict]) -> None:
    _save_collection("domain", rows)


def _find_domain_email(rows: list[dict], email: str) -> dict | None:
    target = (email or "").lower()
    return next((r for r in rows if (r.get("email") or "").lower() == target), None)


def claim_next_domain_email(email: str) -> dict:
    """记录一个新的域名邮箱地址到池中（标记为 available）。"""
    _ensure_sqlite()
    db_source = _EMAIL_SOURCES["domain"]
    target = (email or "").strip()
    with _LOCK:
        with closing(_sqlite_conn()) as conn:
            with conn:
                row_db = conn.execute(
                    "SELECT payload FROM email_pool WHERE source=? AND email=? COLLATE NOCASE LIMIT 1",
                    (db_source, target),
                ).fetchone()
                if row_db:
                    return json.loads(row_db["payload"])
                max_id = conn.execute("SELECT COALESCE(MAX(id), 0) FROM email_pool").fetchone()[0]
                new_id = int(max_id) + 1
                row = {
                    "id": new_id,
                    "email": target,
                    "status": "available",
                    "used_at": None,
                    "note": None,
                    "created_at": _now(),
                }
                conn.execute(
                    "INSERT INTO email_pool(id, email, source, status, archived, created_at, updated_at, payload) "
                    "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
                    (new_id, target, db_source, "available", 0, row["created_at"], "", json.dumps(row, ensure_ascii=False)),
                )
                return dict(row)


def release_domain_email(email: str, status: str = "available", note: str | None = None) -> None:
    """更新域名邮箱状态。"""
    _release_email_in_source("domain", email, status=status, note=note)


def release_unconsumed_domain_email(email: str, note: str | None = None) -> bool:
    """原子回收未生成本地账号且仍为 used 的域名邮箱。"""
    return _release_unconsumed_in_source("domain", email, note=note)


def get_domain_email_by_email(email: str) -> dict | None:
    return _get_email_from_source("domain", email)


def list_domain_email_pool(status: str | None = None, limit: int = 500) -> list[dict]:
    return list_email_pool_page(
        source="cloudflare_domain", status=status, limit=limit, offset=0
    )["items"]


def domain_email_pool_summary() -> dict:
    with _LOCK:
        return _pool_summary_sql("domain")


def delete_domain_email(email: str) -> bool:
    """从域名邮箱池删除一个邮箱。"""
    return delete_email_pool(email, source="cloudflare_domain")


def sync_email_pool_to_emails_txt(target_file: Path | str | None = None) -> int:
    """将 SQLite email_pool 中 outlook 邮箱同步至 emails.txt。"""
    from pathlib import Path
    if target_file is None:
        from .base import _PROJECT_ROOT
        target_path = _PROJECT_ROOT / "emails.txt"
    else:
        target_path = Path(target_file)
    
    rows = _load_outlook()
    lines = []
    seen = set()
    for r in rows:
        email = (r.get("email") or "").strip()
        if not email or email.lower() in seen:
            continue
        seen.add(email.lower())
        pwd = r.get("password") or "x"
        cid = r.get("client_id") or "9e5f94bc-e8a4-4e73-b8be-63364c29d753"
        rt = r.get("refresh_token") or ""
        if rt and cid:
            lines.append(f"{email}----{pwd}----{rt}----{cid}")
        elif pwd:
            lines.append(f"{email}----{pwd}")
        else:
            lines.append(email)
    
    with open(target_path, "w", encoding="utf-8") as f:
        f.write("# turb-gpt-free-register 邮箱池 (自动由 SQLite email_pool 同步)\n")
        f.write("# 格式: 邮箱----密码----refresh_token----client_id\n")
        for line in lines:
            f.write(line + "\n")
    return len(lines)


def import_email_pool(source: str, records: list[dict], as_registered: bool = False) -> int:
    """统一邮箱素材导入入口。"""
    src = (source or "outlook").strip().lower()
    if as_registered:
        inserted, _ = import_registered_email_accounts(records, src)
        return inserted
    if src == "outlook":
        inserted, _ = import_outlook_accounts(records)
        try:
            sync_email_pool_to_emails_txt()
        except Exception:
            pass
        return inserted
    elif src == "generic_api":
        inserted, _ = import_generic_api_emails(records)
        return inserted
    elif src == "imap":
        inserted, _ = import_imap_emails(records)
        return inserted
    elif src == "uned":
        inserted, _ = import_uned_emails(records)
        return inserted
    return 0

