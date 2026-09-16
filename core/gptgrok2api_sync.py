# -*- coding: utf-8 -*-
"""
GPT Grok 2 API 账号与代理组同步模块。

功能：
1. 地区与代理组划分：
   - 日本 / 亚洲 (JP, SG 等) -> 亚洲代理组 (group-19mezl / pg-1c59d0035c)
   - 欧洲 (GB, DE, FR 等)    -> 欧洲代理组 (group-tjyuxo / pg-1b4ff550f8)
   - 美洲 (US, CA 等)        -> 美洲代理组 (group-1d81tz / pg-a428d336a1)
2. 批量推送控制：
   - 默认以 3 个账号为一个批量推送单位 (BATCH_SYNC_SIZE = 3)。
   - 达到批量单位时自动触发批量推送；支持强制全量/补推。
   - 保护代理组长期稳定：账号继承代理组固定代理，不覆盖临时注册会话节点。
3. 高可用同步：
   - 首选调用 gptGrok2api 的 /api/accounts/import-api 接口。
   - 接口不可用时回退原子写入 local accounts.json。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import threading
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

GPTGROK2API_URL = os.environ.get("GPTGROK2API_URL", "http://127.0.0.1:3010/api/accounts/import-api")
GPTGROK2API_KEY = os.environ.get("GPTGROK2API_KEY", "")
GPTGROK2API_DATA_FILE = Path(os.environ.get("GPTGROK2API_DATA_FILE", "accounts.json"))

# 分组常量
GROUP_ID_ASIA = "group-19mezl"     # 亚洲 (对应代理组 pg-1c59d0035c)
GROUP_ID_EUROPE = "group-tjyuxo"   # 欧洲 (对应代理组 pg-1b4ff550f8)
GROUP_ID_AMERICAS = "group-1d81tz" # 美洲 (对应代理组 pg-a428d336a1)

BATCH_SYNC_SIZE = int(os.environ.get("GPTGROK2API_BATCH_SIZE", "3"))

_SYNC_LOCK = threading.Lock()
_DB_PATH = Path(__file__).resolve().parent.parent / "turb.sqlite3"


def map_country_to_group_id(country: str | None, proxy_used: str | None = None) -> str:
    """
    根据注册国家或代理地址提取区域，映射到 gptGrok2api 对应的分组 ID：
    - 日本 / 亚洲 -> 亚洲代理组 (group-19mezl)
    - 欧洲        -> 欧洲代理组 (group-tjyuxo)
    - 美洲        -> 美洲代理组 (group-1d81tz)
    """
    c = str(country or "").strip().upper()
    p = str(proxy_used or "").strip().upper()

    # 1. 直接检查 country 代码
    if c in {"JP", "SG", "HK", "TW", "KR", "MY", "TH", "VN", "ASIA", "JAPAN"}:
        return GROUP_ID_ASIA
    if c in {"GB", "UK", "DE", "FR", "NL", "IT", "ES", "SE", "CH", "PL", "EU", "EUROPE"}:
        return GROUP_ID_EUROPE
    if c in {"US", "CA", "MX", "BR", "AMERICAS", "USA"}:
        return GROUP_ID_AMERICAS

    # 2. 从 proxy_used URL 中提取前缀（例如 http://US.sess_1:... 或 http://JP:...）
    proxy_match = re.search(r"//([A-Z]{2,8})(?:[.:_-]|\b)", p)
    if proxy_match:
        tag = proxy_match.group(1).upper()
        if tag in {"JP", "SG", "HK", "TW", "KR", "ASIA"}:
            return GROUP_ID_ASIA
        if tag in {"GB", "UK", "DE", "FR", "NL", "IT", "ES", "EU", "EUROPE"}:
            return GROUP_ID_EUROPE
        if tag in {"US", "CA", "AMERICAS"}:
            return GROUP_ID_AMERICAS

    # 3. 缺省兜底为美洲代理组
    return GROUP_ID_AMERICAS


def _get_sqlite_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(_DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _extract_account_info(row_dict: dict) -> dict:
    """从数据库 row / payload 提取格式化数据供推送。"""
    payload_str = row_dict.get("payload")
    payload = {}
    if isinstance(payload_str, str) and payload_str.strip():
        try:
            payload = json.loads(payload_str)
        except Exception:
            payload = {}
    elif isinstance(payload_str, dict):
        payload = payload_str

    # 综合取值
    email = row_dict.get("email") or payload.get("email") or ""
    access_token = row_dict.get("access_token") or payload.get("access_token") or ""
    totp_secret = row_dict.get("totp_secret") or payload.get("totp_secret") or ""
    proxy_used = row_dict.get("proxy_used") or payload.get("proxy_used") or ""
    country = row_dict.get("country") or payload.get("country") or ""

    extra = {}
    extra_raw = payload.get("extra_json")
    if isinstance(extra_raw, str) and extra_raw.strip():
        try:
            extra = json.loads(extra_raw)
        except Exception:
            extra = {}
    elif isinstance(extra_raw, dict):
        extra = extra_raw

    password = (
        row_dict.get("password")
        or payload.get("password")
        or extra.get("registration_password")
        or ""
    )

    group_id = map_country_to_group_id(country, proxy_used)

    return {
        "id": row_dict.get("id") or payload.get("id"),
        "email": email,
        "access_token": access_token,
        "login_password": password,
        "two_factor_secret": totp_secret,
        "group_id": group_id,
        "status": "正常",
        "source_type": "web",
        "enabled": True,
        "default_model_slug": "auto",
        "country": country,
        "proxy_used": proxy_used,
        "synced": bool(payload.get("gptgrok2api_synced")),
    }


def get_unpushed_accounts() -> list[dict]:
    """获取所有尚未同步到 gptGrok2api 的有效账号。"""
    if not _DB_PATH.exists():
        return []
    with closing(_get_sqlite_conn()) as conn:
        rows = conn.execute(
            "SELECT id, email, payload FROM accounts WHERE status != 'archived' ORDER BY id ASC"
        ).fetchall()
        accounts = []
        for r in rows:
            info = _extract_account_info(dict(r))
            if info.get("access_token") and not info.get("synced"):
                accounts.append(info)
        return accounts


def mark_accounts_synced(account_ids: list[int]) -> None:
    """在 SQLite 中将指定账号标记为已同步。"""
    if not account_ids or not _DB_PATH.exists():
        return
    now_str = datetime.utcnow().isoformat() + "Z"
    with closing(_get_sqlite_conn()) as conn:
        with conn:
            for acc_id in account_ids:
                row = conn.execute("SELECT payload FROM accounts WHERE id = ?", (acc_id,)).fetchone()
                if row and row["payload"]:
                    try:
                        p = json.loads(row["payload"])
                        p["gptgrok2api_synced"] = True
                        p["gptgrok2api_synced_at"] = now_str
                        conn.execute("UPDATE accounts SET payload = ? WHERE id = ?", (json.dumps(p, ensure_ascii=False), acc_id))
                    except Exception as e:
                        logger.warning("[gptGrok2api] 标记账号 %s 已同步失败: %s", acc_id, e)


def push_accounts_to_gptgrok2api(accounts: list[dict]) -> dict:
    """
    将账号列表推送到 gptGrok2api。
    优先通过 HTTP 导入 API 推送，失败则安全回退至直接更新本地 accounts.json。
    """
    if not accounts:
        return {"ok": True, "added": 0, "skipped": 0, "message": "无待推送账号"}

    clean_payloads = []
    for acc in accounts:
        item = {
            "email": acc["email"],
            "access_token": acc["access_token"],
            "login_password": acc.get("login_password") or "",
            "two_factor_secret": acc.get("two_factor_secret") or "",
            "group_id": acc.get("group_id") or GROUP_ID_AMERICAS,
            "status": "正常",
            "source_type": "web",
            "enabled": True,
            "default_model_slug": "auto",
        }
        clean_payloads.append(item)

    # 1. 尝试 HTTP API 推送
    http_success = False
    result_data = {}
    try:
        resp = requests.post(
            GPTGROK2API_URL,
            headers={
                "X-API-Key": GPTGROK2API_KEY,
                "Content-Type": "application/json",
            },
            json={"accounts": clean_payloads},
            timeout=10,
        )
        if resp.status_code == 200:
            result_data = resp.json()
            http_success = True
            logger.info(
                "[gptGrok2api] HTTP 接口推送成功: added=%s, skipped=%s",
                result_data.get("added"),
                result_data.get("skipped"),
            )
        else:
            logger.warning("[gptGrok2api] HTTP 接口返回异常: %s %s", resp.status_code, resp.text[:200])
    except Exception as exc:
        logger.warning("[gptGrok2api] HTTP 接口连接失败，尝试回退本地文件写入: %s", exc)

    # 2. 若 HTTP API 未成功，回退到写入 accounts.json 文件
    if not http_success:
        try:
            if GPTGROK2API_DATA_FILE.exists():
                existing = json.loads(GPTGROK2API_DATA_FILE.read_text(encoding="utf-8"))
                by_token = {item.get("access_token"): idx for idx, item in enumerate(existing) if item.get("access_token")}
                added, skipped = 0, 0
                for item in clean_payloads:
                    tok = item.get("access_token")
                    if tok in by_token:
                        # 更新已有记录
                        idx = by_token[tok]
                        existing[idx].update(item)
                        skipped += 1
                    else:
                        item["created_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                        existing.append(item)
                        by_token[tok] = len(existing) - 1
                        added += 1
                GPTGROK2API_DATA_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
                result_data = {"added": added, "skipped": skipped, "mode": "direct_file"}
                logger.info("[gptGrok2api] 本地 accounts.json 写入成功: added=%s, skipped=%s", added, skipped)
            else:
                return {"ok": False, "error": f"API 无法连接且数据文件不存在: {GPTGROK2API_DATA_FILE}"}
        except Exception as file_exc:
            logger.exception("[gptGrok2api] 写入本地 accounts.json 失败: %s", file_exc)
            return {"ok": False, "error": str(file_exc)}

    # 3. 标记 SQLite 账号为已同步
    synced_ids = [acc["id"] for acc in accounts if acc.get("id")]
    mark_accounts_synced(synced_ids)

    return {
        "ok": True,
        "added": result_data.get("added", 0),
        "skipped": result_data.get("skipped", 0),
        "count": len(accounts),
        "accounts": [a["email"] for a in accounts],
    }


def on_account_completed(email: str, force_check: bool = False) -> dict:
    """
    当单个账号注册（或更新 2FA）完成时调用。
    检查当前待推送账号数量：
    - 若达到 BATCH_SYNC_SIZE（默认 3 个）或 force_check=True，则执行批量推送。
    - 否则记录待推送状态，等待批次凑齐。
    """
    with _SYNC_LOCK:
        unpushed = get_unpushed_accounts()
        count = len(unpushed)
        if count >= BATCH_SYNC_SIZE or (force_check and count > 0):
            logger.info(
                "[gptGrok2api] 触发批量推送 (待推送数 %d >= 批次单位 %d): %s",
                count,
                BATCH_SYNC_SIZE,
                [a["email"] for a in unpushed],
            )
            return push_accounts_to_gptgrok2api(unpushed)
        else:
            logger.info(
                "[gptGrok2api] 待推送账号进度: %d/%d (当前账号: %s)，将在达到批次单位后自动推送",
                count,
                BATCH_SYNC_SIZE,
                email,
            )
            return {"ok": True, "pending": count, "batch_size": BATCH_SYNC_SIZE, "pushed": False}


def sync_all_registered_accounts(force: bool = True) -> dict:
    """
    全量或强制同步当前 SQLite 中的所有注册账号到 gptGrok2api。
    常用于一键补推历史账号或强制清空待推送队列。
    """
    with _SYNC_LOCK:
        if not _DB_PATH.exists():
            return {"ok": False, "error": "turb.sqlite3 不存在"}
        with closing(_get_sqlite_conn()) as conn:
            rows = conn.execute(
                "SELECT id, email, payload FROM accounts WHERE status != 'archived' ORDER BY id ASC"
            ).fetchall()
            accounts = []
            for r in rows:
                info = _extract_account_info(dict(r))
                if info.get("access_token"):
                    if force or not info.get("synced"):
                        accounts.append(info)
            if not accounts:
                return {"ok": True, "added": 0, "skipped": 0, "message": "没有需要同步的账号"}
            logger.info("[gptGrok2api] 开始全量/补推同步，共 %d 个账号...", len(accounts))
            return push_accounts_to_gptgrok2api(accounts)
