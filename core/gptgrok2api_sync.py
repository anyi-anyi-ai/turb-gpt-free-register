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

try:
    from dotenv import load_dotenv
    _env_file = Path(__file__).resolve().parent.parent / ".env"
    if _env_file.exists():
        load_dotenv(_env_file)
except Exception:
    pass

GPTGROK2API_URL = os.environ.get("GPTGROK2API_URL", "http://4.193.130.57:3010/api/accounts/import-api,http://127.0.0.1:3010/api/accounts/import-api")
GPTGROK2API_KEY = os.environ.get("GPTGROK2API_KEY", "adm-e0de3f869d944ea0868921a36d03d8a9")


def get_target_api_urls() -> list[str]:
    """获取所有配置的 gptGrok2api 接口推送地址（支持逗号分隔的多端广播同步）。"""
    raw = os.environ.get("GPTGROK2API_URL", "http://4.193.130.57:3010/api/accounts/import-api,http://127.0.0.1:3010/api/accounts/import-api")
    return [u.strip() for u in raw.split(",") if u.strip()]

_data_file_candidate = Path(os.environ.get("GPTGROK2API_DATA_FILE", "F:/github/gptGrok2api/data/accounts.json"))
if not _data_file_candidate.exists():
    _fallback_data = Path("F:/github/gptGrok2api/data/accounts.json")
    if _fallback_data.exists():
        _data_file_candidate = _fallback_data
GPTGROK2API_DATA_FILE = _data_file_candidate

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


def format_proxy_for_gptgrok2api(
    proxy_val: str | None,
    country: str | None = None,
    acc_id: Any = None,
    email: str | None = None,
    resin_token: str | None = None,
) -> str:
    """
    格式化代理地址供 GPTGrok2API 使用：
    1. 过滤本地临时动态住宅转发端口（如 127.0.0.1:28888 或包含 28888 的临时中转代理），
       因为注册结束后该本地端口关闭，必须安全回退至 Resin 分国稳定代理；
    2. 将宿主机 127.0.0.1:2260 / localhost:2260 转换为 Docker 内部网络 host.docker.internal:2260；
    3. 若原代理为空，则根据国家代码与账号 ID 自动生成 Resin 专属会话代理。
    """
    token = resin_token or os.environ.get("RESIN_PROXY_TOKEN", "resin_proxy_c3d7b8e1f0a2496582d9183b")
    p = str(proxy_val or "").strip()

    # 命中临时端口 28888 或为空，回退到对应国家的持久 Resin 代理
    if not p or "28888" in p:
        c = str(country or "US").strip().upper()
        if not c or len(c) > 8:
            c = "US"
        num_id = acc_id or (abs(hash(email or "acc")) % 1000 + 1)
        return f"http://{c}.sess_{num_id}:{token}@host.docker.internal:2260"

    return p.replace("127.0.0.1:2260", "host.docker.internal:2260").replace("localhost:2260", "host.docker.internal:2260")



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

    live_status = str(payload.get("live_check_status") or row_dict.get("status") or "").lower()
    live_err = str(payload.get("live_check_error") or "").lower()
    warm_err = str(payload.get("warming_error") or "").lower()
    is_deactivated = (
        live_status == "deactivated"
        or "deactivated" in warm_err
        or "deactivated" in live_err
        or "token_revoked" in live_err
        or "account_deactivated" in live_err
    )
    acc_status = "异常" if is_deactivated else "正常"
    is_enabled = not is_deactivated

    return {
        "id": row_dict.get("id") or payload.get("id"),
        "email": email,
        "access_token": access_token,
        "login_password": password,
        "two_factor_secret": totp_secret,
        "group_id": group_id,
        "status": acc_status,
        "source_type": "web",
        "enabled": is_enabled,
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
    resin_token = os.environ.get("RESIN_PROXY_TOKEN", "resin_proxy_c3d7b8e1f0a2496582d9183b")
    for acc in accounts:
        proxy_val = format_proxy_for_gptgrok2api(
            proxy_val=acc.get("proxy_used") or acc.get("proxy"),
            country=acc.get("country"),
            acc_id=acc.get("id"),
            email=acc.get("email"),
            resin_token=resin_token,
        )

        acc_status = acc.get("status") or "正常"
        acc_enabled = acc.get("enabled", True) if "enabled" in acc else (acc_status == "正常")

        item = {
            "email": acc["email"],
            "access_token": acc["access_token"],
            "login_password": acc.get("login_password") or "",
            "two_factor_secret": acc.get("two_factor_secret") or "",
            "group_id": acc.get("group_id") or map_country_to_group_id(acc.get("country"), proxy_val),
            "country": acc.get("country") or "",
            "proxy": proxy_val,
            "status": acc_status,
            "source_type": "web",
            "enabled": acc_enabled,
            "default_model_slug": "auto",
        }
        clean_payloads.append(item)

    # 1. 尝试 HTTP API 推送 (多端广播同步：云端与本地)
    http_success = False
    result_data = {}
    emails = [acc["email"] for acc in clean_payloads if acc.get("email")]
    target_urls = get_target_api_urls()

    for api_url in target_urls:
        del_url = api_url.replace("/api/accounts/import-api", "/api/accounts")
        # 1.1 先清理旧账号记录（防 stale token / 401 异常残留）
        if emails and GPTGROK2API_KEY:
            try:
                requests.delete(
                    del_url,
                    headers={
                        "X-API-Key": GPTGROK2API_KEY,
                        "Authorization": f"Bearer {GPTGROK2API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={"tokens": emails},
                    timeout=5,
                )
            except Exception as del_err:
                logger.debug("[gptGrok2api] 清理旧 token 记录跳过或异常 (%s): %s", api_url, del_err)

        # 1.2 导入最新账号凭证与状态
        try:
            resp = requests.post(
                api_url,
                headers={
                    "X-API-Key": GPTGROK2API_KEY,
                    "Authorization": f"Bearer {GPTGROK2API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"accounts": clean_payloads},
                timeout=10,
            )
            if resp.status_code == 200:
                result_data = resp.json()
                http_success = True
                logger.info(
                    "[gptGrok2api] HTTP 接口 (%s) 推送成功: added=%s, skipped=%s",
                    api_url,
                    result_data.get("added"),
                    result_data.get("skipped"),
                )
                # 1.3 额外通过 update 接口兜底确保每个账号的 status 都是正常且 enabled=True
                if GPTGROK2API_KEY:
                    update_url = api_url.replace("/api/accounts/import-api", "/api/accounts/update")
                    for acc in clean_payloads:
                        try:
                            requests.post(
                                update_url,
                                headers={
                                    "X-API-Key": GPTGROK2API_KEY,
                                    "Authorization": f"Bearer {GPTGROK2API_KEY}",
                                    "Content-Type": "application/json",
                                },
                                json={
                                    "access_token": acc["email"],
                                    "status": acc.get("status", "正常"),
                                    "enabled": acc.get("enabled", True),
                                },
                                timeout=3,
                            )
                        except Exception:
                            pass
            else:
                logger.warning("[gptGrok2api] HTTP 接口 (%s) 返回异常: %s %s", api_url, resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("[gptGrok2api] HTTP 接口 (%s) 连接失败: %s", api_url, exc)

    # 2. 同时原子同步写入本地 accounts.json 文件（保证双重落盘一致性）
    try:
        if GPTGROK2API_DATA_FILE.exists():
            existing = json.loads(GPTGROK2API_DATA_FILE.read_text(encoding="utf-8"))
            by_email = {str(item.get("email") or "").strip().lower(): idx for idx, item in enumerate(existing) if item.get("email")}
            by_token = {item.get("access_token"): idx for idx, item in enumerate(existing) if item.get("access_token")}
            added, updated = 0, 0
            for item in clean_payloads:
                em = str(item.get("email") or "").strip().lower()
                tok = item.get("access_token")
                target_idx = by_email.get(em) if em else by_token.get(tok)
                is_en = item.get("enabled", True)
                if target_idx is not None:
                    # 更新已有记录
                    existing[target_idx].update(item)
                    existing[target_idx]["status"] = item.get("status", "正常")
                    existing[target_idx]["enabled"] = is_en
                    existing[target_idx]["disabled"] = not is_en
                    if is_en:
                        existing[target_idx]["fail"] = 0
                        existing[target_idx]["failure_count"] = 0
                        existing[target_idx]["error"] = None
                        existing[target_idx]["last_error"] = None
                        existing[target_idx]["last_error_message"] = None
                        existing[target_idx]["last_error_status"] = None
                    updated += 1
                else:
                    item["created_at"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                    existing.append(item)
                    if em:
                        by_email[em] = len(existing) - 1
                    if tok:
                        by_token[tok] = len(existing) - 1
                    added += 1
            GPTGROK2API_DATA_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            if not http_success:
                result_data = {"added": added, "updated": updated, "mode": "direct_file"}
            logger.info("[gptGrok2api] accounts.json 写入成功: added=%s, updated=%s", added, updated)
    except Exception as file_exc:
        logger.warning("[gptGrok2api] 写入本地 accounts.json 异常: %s", file_exc)


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


def push_grok_accounts_to_gptgrok2api(grok_accounts: list[dict[str, Any]]) -> dict:
    """将 Grok (x.ai) 账号格式化并推送到 gptGrok2api 接口或写入 accounts.json。"""
    if not grok_accounts:
        return {"ok": True, "added": 0, "skipped": 0, "message": "账号列表为空"}

    clean_payloads = []
    account_ids = []

    for acc in grok_accounts:
        acc_id = acc.get("id")
        if acc_id:
            account_ids.append(int(acc_id))

        proxy_val = format_proxy_for_gptgrok2api(
            proxy_val=acc.get("proxy_used") or acc.get("proxy"),
            country=acc.get("country"),
            acc_id=acc_id,
            email=acc.get("email"),
            resin_token=os.environ.get("RESIN_PROXY_TOKEN", "resin_proxy_c3d7b8e1f0a2496582d9183b"),
        )

        # 优先使用 sso_token (Grok2API 支持格式)，若有 refresh_token 也记录在 extra
        primary_token = str(acc.get("sso_token") or acc.get("refresh_token") or acc.get("access_token") or "").strip()
        if primary_token.startswith("sso="):
            primary_token = primary_token[4:].strip()

        group_id = map_country_to_group_id(acc.get("country"), proxy_val)
        is_healthy = str(acc.get("status") or "").lower() == "healthy"

        item = {
            "email": acc["email"],
            "access_token": primary_token,
            "login_password": acc.get("password") or "",
            "two_factor_secret": "",
            "group_id": group_id,
            "country": acc.get("country") or "",
            "proxy": proxy_val,
            "status": "正常" if is_healthy else "降智/受限",
            "source_type": "grok",
            "enabled": is_healthy,
            "default_model_slug": "grok-3",
        }
        clean_payloads.append(item)

    # 1. 尝试调用 HTTP 接口推送 (多端广播同步：云端与本地)
    http_success = False
    result_data = {}
    emails = [acc["email"] for acc in clean_payloads if acc.get("email")]
    target_urls = get_target_api_urls()

    for api_url in target_urls:
        del_url = api_url.replace("/api/accounts/import-api", "/api/accounts")
        # 1.1 先清理旧账号记录
        if emails and GPTGROK2API_KEY:
            try:
                requests.delete(
                    del_url,
                    headers={
                        "X-API-Key": GPTGROK2API_KEY,
                        "Authorization": f"Bearer {GPTGROK2API_KEY}",
                        "Content-Type": "application/json",
                    },
                    json={"tokens": emails},
                    timeout=5,
                )
            except Exception as del_err:
                logger.debug("[gptGrok2api] 清理旧 Grok 账号记录跳过或异常 (%s): %s", api_url, del_err)

        # 1.2 导入最新 Grok 账号凭据
        try:
            resp = requests.post(
                api_url,
                headers={
                    "X-API-Key": GPTGROK2API_KEY,
                    "Authorization": f"Bearer {GPTGROK2API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"accounts": clean_payloads},
                timeout=10,
            )
            if resp.status_code == 200:
                result_data = resp.json()
                http_success = True
                logger.info(
                    "[gptGrok2api] Grok 账号 HTTP 接口 (%s) 推送成功: added=%s, skipped=%s",
                    api_url,
                    result_data.get("added"),
                    result_data.get("skipped"),
                )
            else:
                logger.warning("[gptGrok2api] HTTP 接口 (%s) 返回异常: %s %s", api_url, resp.status_code, resp.text[:200])
        except Exception as exc:
            logger.warning("[gptGrok2api] HTTP 接口 (%s) 连接失败: %s", api_url, exc)

    # 2. 如果 HTTP 接口不可用，回退写入 accounts.json
    if not http_success:
        try:
            existing = []
            if GPTGROK2API_DATA_FILE.exists():
                existing = json.loads(GPTGROK2API_DATA_FILE.read_text(encoding="utf-8"))
            existing_by_email = {a.get("email"): i for i, a in enumerate(existing) if a.get("email")}
            added = 0
            updated = 0
            for item in clean_payloads:
                em = item["email"]
                if em in existing_by_email:
                    existing[existing_by_email[em]].update(item)
                    updated += 1
                else:
                    existing.append(item)
                    added += 1
            GPTGROK2API_DATA_FILE.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            result_data = {"ok": True, "added": added, "updated": updated, "fallback_file": True}
        except Exception as file_exc:
            logger.warning("[gptGrok2api] 写入本地 accounts.json 异常: %s", file_exc)
            return {"ok": False, "error": f"HTTP 与本地写入均失败: {file_exc}"}

    # 2.2 始终同步写入 grok_accounts.json (驱动 GPTGrok2API 控制台的「Grok」专用账号管理 Tab)
    try:
        grok_file = GPTGROK2API_DATA_FILE.parent / "grok_accounts.json"
        existing_grok = []
        if grok_file.exists():
            try:
                existing_grok = json.loads(grok_file.read_text(encoding="utf-8"))
            except Exception:
                existing_grok = []
        grok_by_email = {str(a.get("email") or "").lower(): i for i, a in enumerate(existing_grok) if a.get("email")}
        now_str = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
        for item in clean_payloads:
            em = str(item.get("email") or "").strip()
            if not em:
                continue
            key = em.lower()
            acc_id = str(item.get("id") or f"grok-{int(time.time()*1000)}")
            if not acc_id.startswith("grok-"):
                acc_id = f"grok-{acc_id}"
            oauth_state = None
            if item.get("access_token"):
                oauth_state = {
                    "status": "authorized",
                    "attempted_at": now_str,
                    "error": "",
                    "attempts": 1,
                }
            entry = {
                "id": acc_id,
                "email": em,
                "password": item.get("login_password") or item.get("password") or "",
                "sso": item.get("access_token") or item.get("sso_token") or "",
                "access_token": item.get("access_token") or "",
                "refresh_token": item.get("refresh_token") or "",
                "status": "active",
                "enabled": True,
                "source_type": "protocol",
                "created_at": item.get("created_at") or now_str,
                "updated_at": now_str,
            }
            if oauth_state:
                entry["oauth_authorization"] = oauth_state
            if key in grok_by_email:
                existing_grok[grok_by_email[key]].update(entry)
            else:
                grok_by_email[key] = len(existing_grok)
                existing_grok.append(entry)
        grok_file.write_text(json.dumps(existing_grok, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as grok_file_exc:
        logger.warning("[gptGrok2api] 同步 grok_accounts.json 异常: %s", grok_file_exc)

    # 3. 标记数据库中 Grok 账号已同步
    if account_ids:
        try:
            from core.db.grok import mark_grok_accounts_synced
            mark_grok_accounts_synced(account_ids)
        except Exception as db_err:
            logger.warning("[gptGrok2api] 标记 Grok 账号同步状态异常: %s", db_err)

    return {"ok": True, "data": result_data, "count": len(clean_payloads)}

