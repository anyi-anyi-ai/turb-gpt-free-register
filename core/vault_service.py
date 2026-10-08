# -*- coding: utf-8 -*-
"""跨平台通用资产金库服务 (Universal Vault Service)。

统一抽象并接管 turb.sqlite3 中 10+ 个平台的账号资产读写、状态统计、
敏感字段按需解密获取、多格式导出 (Sub2API / OpenAI / CSV / TXT)。
"""
from __future__ import annotations

import csv
import io
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core import db

logger = logging.getLogger(__name__)

# 支持的平台及其底层数据表元数据
PLATFORM_META: Dict[str, Dict[str, Any]] = {
    "chatgpt": {
        "table": "accounts",
        "primary_id": "id",
        "account_id_col": "email",
        "status_col": "status",
        "is_payload_based": True,
    },
    "grok": {
        "table": "grok_accounts",
        "primary_id": "id",
        "account_id_col": "email",
        "status_col": "status",
        "is_payload_based": False,
    },
    "github": {
        "table": "github_accounts",
        "primary_id": "id",
        "account_id_col": "username",
        "alt_id_col": "email",
        "status_col": "status",
        "is_payload_based": False,
    },
    "uned": {
        "table": "uned_accounts",
        "primary_id": "id",
        "account_id_col": "email",
        "alt_id_col": "uned_identifier",
        "status_col": "status",
        "is_payload_based": False,
    },
    "suno": {
        "table": "suno_accounts",
        "primary_id": "id",
        "account_id_col": "email",
        "status_col": "status",
        "is_payload_based": False,
    },
    "elevenlabs": {
        "table": "elevenlabs_accounts",
        "primary_id": "id",
        "account_id_col": "email",
        "status_col": "status",
        "is_payload_based": False,
    },
    "udio": {
        "table": "udio_accounts",
        "primary_id": "id",
        "account_id_col": "email",
        "alt_id_col": "username",
        "status_col": "status",
        "is_payload_based": False,
    },
    "stableaudio": {
        "table": "stableaudio_accounts",
        "primary_id": "id",
        "account_id_col": "email",
        "status_col": "status",
        "is_payload_based": False,
    },
    "muse": {
        "table": "muse_accounts",
        "primary_id": "id",
        "account_id_col": "email",
        "status_col": "status",
        "is_payload_based": False,
    },
    "codex": {
        "table": "codex_accounts",
        "primary_id": "id",
        "account_id_col": "email",
        "alt_id_col": "filename",
        "status_col": "status",
        "is_payload_based": True,
    },
}


def _parse_row(platform: str, row: Dict[str, Any]) -> Dict[str, Any]:
    """将不同表的原始 row 标准化转换为通用脱敏账号模型。"""
    meta = PLATFORM_META.get(platform, {})
    is_payload = meta.get("is_payload_based", False)
    payload_data: Dict[str, Any] = {}
    if is_payload and "payload" in row:
        raw_p = row.get("payload")
        if isinstance(raw_p, str) and raw_p.strip():
            try:
                payload_data = json.loads(raw_p)
            except Exception:
                payload_data = {}
        elif isinstance(raw_p, dict):
            payload_data = raw_p

    merged = {**row, **payload_data}
    acc_id_col = meta.get("account_id_col", "email")
    alt_col = meta.get("alt_id_col")
    account_id = merged.get(acc_id_col) or (merged.get(alt_col) if alt_col else "") or f"id_{row.get('id')}"

    # 凭据拥有标识（零明文泄露）
    has_token = bool(
        str(merged.get("access_token") or merged.get("sso_token") or merged.get("session_token") or merged.get("api_key") or "").strip()
    )
    has_password = bool(str(merged.get("password") or merged.get("account_password") or "").strip())
    has_totp = bool(str(merged.get("totp_secret") or "").strip())
    has_cookie = bool(str(merged.get("cookies_json") or "").strip())

    # 提取特征指标
    metrics: Dict[str, Any] = {}
    if platform == "chatgpt":
        metrics["plan_type"] = merged.get("plan_type") or "free"
        metrics["codex_ready"] = bool(merged.get("codex_agent_token"))
    elif platform == "elevenlabs":
        metrics["credits"] = merged.get("credits", 0)
    elif platform == "uned":
        metrics["uned_id"] = merged.get("uned_identifier")
        metrics["doc_number"] = merged.get("doc_number")
    elif platform == "github":
        metrics["username"] = merged.get("username")
    elif platform == "udio":
        metrics["username"] = merged.get("username")
    elif platform == "grok":
        metrics["has_sso_rw"] = bool(merged.get("sso_rw"))

    status = str(merged.get(meta.get("status_col", "status")) or "registered").lower()
    created_at = str(merged.get("created_at") or "")
    updated_at = str(merged.get("updated_at") or created_at)

    return {
        "id": row.get("id"),
        "platform": platform,
        "account_id": str(account_id),
        "status": status,
        "has_token": has_token,
        "has_password": has_password,
        "has_totp": has_totp,
        "has_cookie": has_cookie,
        "metrics": metrics,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def list_accounts(
    platform: Optional[str] = None,
    query: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    page_size: int = 50,
) -> Dict[str, Any]:
    """多平台统一账号查询与分页。"""
    page = max(1, int(page or 1))
    page_size = max(1, min(500, int(page_size or 50)))
    target_platforms = [platform] if platform and platform in PLATFORM_META else list(PLATFORM_META.keys())

    all_items: List[Dict[str, Any]] = []

    conn = db.base._sqlite_conn()
    try:
        for p in target_platforms:
            meta = PLATFORM_META[p]
            table = meta["table"]
            try:
                # 检查表是否存在
                cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
                if not cur.fetchone():
                    continue

                rows = conn.execute(f"SELECT * FROM `{table}`").fetchall()
                for r in rows:
                    parsed = _parse_row(p, dict(r))
                    # 过滤条件
                    if status and parsed["status"] != status.lower():
                        continue
                    if query:
                        q = query.strip().lower()
                        if q not in parsed["account_id"].lower() and q not in json.dumps(parsed["metrics"]).lower():
                            continue
                    all_items.append(parsed)
            except Exception as e:
                logger.warning("查询平台 %s 表 %s 失败: %s", p, table, e)
    finally:
        conn.close()

    # 按照更新时间倒序排序
    all_items.sort(key=lambda x: x.get("updated_at") or x.get("created_at") or "", reverse=True)
    total = len(all_items)
    offset = (page - 1) * page_size
    paged = all_items[offset : offset + page_size]

    return {
        "ok": True,
        "items": paged,
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size if total > 0 else 1,
    }


def get_account_secret(platform: str, account_id: Any, field: str = "all") -> Dict[str, Any]:
    """安全读取单个账号的机密凭据信息。"""
    if platform not in PLATFORM_META:
        return {"ok": False, "error": f"不支持的平台: {platform}"}

    meta = PLATFORM_META[platform]
    table = meta["table"]
    conn = db.base._sqlite_conn()
    try:
        # 支持按自增 ID 或 account_id 查询
        query_sql = f"SELECT * FROM `{table}` WHERE id = ? OR {meta['account_id_col']} = ?"
        row = conn.execute(query_sql, (account_id, str(account_id))).fetchone()
        if not row:
            if meta.get("alt_id_col"):
                query_sql = f"SELECT * FROM `{table}` WHERE {meta['alt_id_col']} = ?"
                row = conn.execute(query_sql, (str(account_id),)).fetchone()
        if not row:
            return {"ok": False, "error": f"未找到账号: {account_id}"}

        rdict = dict(row)
        if meta.get("is_payload_based") and "payload" in rdict:
            try:
                payload = json.loads(rdict["payload"]) if isinstance(rdict["payload"], str) else rdict["payload"]
                rdict.update(payload)
            except Exception:
                pass

        secrets = {
            "password": rdict.get("password") or rdict.get("account_password") or "",
            "access_token": rdict.get("access_token") or rdict.get("sso_token") or rdict.get("api_key") or "",
            "refresh_token": rdict.get("refresh_token") or "",
            "session_token": rdict.get("session_token") or rdict.get("sso_rw") or "",
            "totp_secret": rdict.get("totp_secret") or "",
            "cookies": rdict.get("cookies_json") or "",
        }

        if field != "all" and field in secrets:
            return {"ok": True, "field": field, "value": secrets[field]}
        return {"ok": True, "secrets": secrets}
    finally:
        conn.close()


def get_vault_metrics() -> Dict[str, Any]:
    """汇总全库所有平台的账号资产健康与数量指标。"""
    conn = db.base._sqlite_conn()
    total_all = 0
    platform_stats: Dict[str, Dict[str, Any]] = {}
    today_str = datetime.now().strftime("%Y-%m-%d")
    today_added = 0

    try:
        for p, meta in PLATFORM_META.items():
            table = meta["table"]
            try:
                cur = conn.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
                if not cur.fetchone():
                    continue

                rows = conn.execute(f"SELECT * FROM `{table}`").fetchall()
                count = len(rows)
                total_all += count

                active_count = 0
                today_count = 0
                for r in rows:
                    rd = dict(r)
                    status = str(rd.get("status") or "").lower()
                    if status in ("active", "registered", "ready", "ok", "success"):
                        active_count += 1
                    created_at = str(rd.get("created_at") or "")
                    if today_str in created_at:
                        today_count += 1

                today_added += today_count
                platform_stats[p] = {
                    "total": count,
                    "active": active_count,
                    "today_added": today_count,
                }
            except Exception as e:
                logger.warning("统计平台 %s 失败: %s", p, e)
    finally:
        conn.close()

    return {
        "ok": True,
        "total_accounts": total_all,
        "today_added": today_added,
        "platforms": platform_stats,
    }


def export_accounts(platform: Optional[str] = None, format_type: str = "sub2api") -> Dict[str, Any]:
    """按指定规范格式导出账号资产 (Sub2API / OpenAI / CSV / TXT)。"""
    res = list_accounts(platform=platform, page=1, page_size=5000)
    items = res.get("items", [])
    format_type = format_type.lower()

    if format_type == "sub2api":
        # 符合 Wei-Shaw/sub2api 导入规范
        sub2_list = []
        for it in items:
            sec = get_account_secret(it["platform"], it["id"])
            secrets = sec.get("secrets", {}) if sec.get("ok") else {}
            sub2_list.append({
                "platform": it["platform"],
                "account": it["account_id"],
                "token": secrets.get("access_token"),
                "refresh_token": secrets.get("refresh_token"),
                "status": it["status"],
                "metrics": it["metrics"],
                "updated_at": it["updated_at"],
            })
        return {
            "ok": True,
            "filename": f"export_{platform or 'all'}_sub2api.json",
            "mimetype": "application/json",
            "content": json.dumps(sub2_list, ensure_ascii=False, indent=2),
        }

    elif format_type == "openai":
        # 符合 OpenAI / ChatGPT-to-API 网关规范 (Bearer Token 列表)
        tokens = []
        for it in items:
            sec = get_account_secret(it["platform"], it["id"])
            if sec.get("ok"):
                tok = sec.get("secrets", {}).get("access_token")
                if tok:
                    tokens.append(tok)
        return {
            "ok": True,
            "filename": f"export_{platform or 'all'}_openai_tokens.txt",
            "mimetype": "text/plain",
            "content": "\n".join(tokens),
        }

    elif format_type == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["platform", "account_id", "status", "password", "access_token", "created_at"])
        for it in items:
            sec = get_account_secret(it["platform"], it["id"])
            s = sec.get("secrets", {}) if sec.get("ok") else {}
            writer.writerow([
                it["platform"],
                it["account_id"],
                it["status"],
                s.get("password", ""),
                s.get("access_token", ""),
                it["created_at"],
            ])
        return {
            "ok": True,
            "filename": f"export_{platform or 'all'}.csv",
            "mimetype": "text/csv",
            "content": output.getvalue(),
        }

    else:  # text 格式
        lines = []
        for it in items:
            sec = get_account_secret(it["platform"], it["id"])
            s = sec.get("secrets", {}) if sec.get("ok") else {}
            pwd = s.get("password") or "NO_PWD"
            tok = s.get("access_token") or "NO_TOKEN"
            lines.append(f"{it['platform']}----{it['account_id']}----{pwd}----{tok}")
        return {
            "ok": True,
            "filename": f"export_{platform or 'all'}.txt",
            "mimetype": "text/plain",
            "content": "\n".join(lines),
        }


def delete_account(platform: str, account_id: Any) -> Dict[str, Any]:
    """从数据库中删除指定账号。"""
    if platform not in PLATFORM_META:
        return {"ok": False, "error": f"不支持的平台: {platform}"}
    meta = PLATFORM_META[platform]
    table = meta["table"]
    conn = db.base._sqlite_conn()
    try:
        with conn:
            conn.execute(f"DELETE FROM `{table}` WHERE id = ? OR {meta['account_id_col']} = ?", (account_id, str(account_id)))
        return {"ok": True, "message": f"成功删除 {platform} 账号 {account_id}"}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        conn.close()
