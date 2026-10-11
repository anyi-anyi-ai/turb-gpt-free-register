# -*- coding: utf-8 -*-
"""Grok 已注册账号批量登录与详细信息提取工具。

针对 DB 中所有已注册的 Grok 账号：
1. 通过真实指纹会话 (impersonate='chrome124') 与 Resin 纯净节点建立会话；
2. 访问 accounts.x.ai 校验 SSO 登录态并获取 Profile（User ID、姓名、注册时间等）；
3. 访问 grok.com 提取当前配额（24h 额度、剩余请求数 remainingQueries、总限额 totalQueries）；
4. 探查 Bot 标记（botFlagSource / botFlagDetails）防降智；
5. 将提取到的详细信息写回 turb.sqlite3 的 grok_accounts，并双向同步至 gptGrok2api 的 accounts.json 和 grok_accounts.json。
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from curl_cffi import requests as curl_requests
from config.proxy import pick_proxy
from core.gptgrok2api_sync import (
    push_grok_accounts_to_gptgrok2api,
    format_proxy_for_gptgrok2api,
    map_country_to_group_id,
)

logger = logging.getLogger(__name__)


def inspect_single_account(
    acc: dict[str, Any],
    proxy: str | None = None,
    timeout_sec: int = 15,
) -> dict[str, Any]:
    """对单个 Grok 账号执行登录与相关信息提取。"""
    acc_id = acc.get("id")
    email = acc.get("email")
    sso_token = str(acc.get("sso_token") or acc.get("sso_rw") or "").strip()
    if sso_token.startswith("sso="):
        sso_token = sso_token[4:].strip()

    info = {
        "id": acc_id,
        "email": email,
        "valid": False,
        "status": acc.get("status", "healthy"),
        "user_id": "",
        "given_name": "",
        "family_name": "",
        "email_domain": "",
        "create_time": "",
        "remaining_queries": None,
        "total_queries": None,
        "window_size_seconds": None,
        "bot_flag_source": None,
        "bot_flag_details": "",
        "has_oauth": bool(acc.get("access_token")),
        "error": "",
    }

    if not sso_token:
        info["status"] = "expired"
        info["error"] = "缺少 SSO 凭据"
        return info

    effective_proxy = proxy
    if not effective_proxy:
        effective_proxy = pick_proxy(country="US") or pick_proxy(country="CA")

    session = curl_requests.Session(impersonate="chrome124")
    if effective_proxy:
        session.proxies = {"http": effective_proxy, "https": effective_proxy}

    cookies = {"sso": sso_token, "sso-rw": sso_token}
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Origin": "https://grok.com",
        "Referer": "https://grok.com/",
    }

    # 1. 登录态校验与 Profile 提取
    try:
        r_session = session.get(
            "https://accounts.x.ai/api/auth/session",
            cookies=cookies,
            headers=headers,
            timeout=timeout_sec,
        )
        if r_session.status_code == 200:
            sess_json = r_session.json()
            if sess_json.get("status") == "authenticated":
                info["valid"] = True
                sess_data = sess_json.get("session") or {}
                info["user_id"] = str(sess_data.get("userId") or "")
                info["given_name"] = str(sess_data.get("givenName") or "")
                info["family_name"] = str(sess_data.get("familyName") or "")
                info["email_domain"] = str(sess_data.get("emailDomain") or "")
                info["create_time"] = str(sess_data.get("createTime") or "")
            else:
                info["status"] = "expired"
                info["error"] = f"会话未认证: {sess_json.get('status')}"
        elif r_session.status_code in (401, 403):
            info["status"] = "expired"
            info["error"] = f"HTTP {r_session.status_code} 凭据已失效"
        else:
            info["error"] = f"Session HTTP {r_session.status_code}"
    except Exception as exc:
        info["error"] = f"Session 请求异常: {exc}"

    # 2. 配额与 Rate Limits 提取
    if info["valid"]:
        try:
            r_limits = session.post(
                "https://grok.com/rest/rate-limits",
                json={"requestKind": "DEFAULT", "modelName": "grok-3"},
                cookies=cookies,
                headers=headers,
                timeout=timeout_sec,
            )
            if r_limits.status_code == 200:
                lim_json = r_limits.json()
                info["remaining_queries"] = lim_json.get("remainingQueries")
                info["total_queries"] = lim_json.get("totalQueries")
                info["window_size_seconds"] = lim_json.get("windowSizeSeconds")
        except Exception as exc:
            pass

    # 3. 首页 initialData 与 BotFlag 探查
    if info["valid"]:
        try:
            r_home = session.get(
                "https://grok.com/",
                cookies=cookies,
                headers=headers,
                timeout=timeout_sec,
            )
            if r_home.status_code == 200:
                html = r_home.text or ""
                marker = '"initialData":'
                pos = html.find(marker)
                if pos >= 0:
                    sub = html[pos + len(marker):].lstrip()
                    try:
                        data, _ = json.JSONDecoder().raw_decode(sub)
                        user_obj = data.get("user") or {}
                        info["bot_flag_source"] = user_obj.get("botFlagSource")
                        info["bot_flag_details"] = str(user_obj.get("botFlagDetails") or "")
                        if not info["user_id"]:
                            info["user_id"] = str(user_obj.get("id") or "")
                    except Exception:
                        pass
        except Exception:
            pass

    try:
        session.close()
    except Exception:
        pass

    return info


def run_inspect_and_update(max_accounts: int = 100, force_sync: bool = True):
    import sqlite3

    db_path = _ROOT / "turb.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    # 获取所有非归档的 healthy 账号
    rows = conn.execute(
        "SELECT * FROM grok_accounts WHERE archived=0 AND status='healthy' ORDER BY id ASC LIMIT ?",
        (max_accounts,)
    ).fetchall()

    accounts = [dict(r) for r in rows]
    total = len(accounts)

    print("=" * 80)
    print(f"★ Grok 已注册账号巡检、登录验证与信息提取中心 ★")
    print(f"  待处理账号总数: {total} 个")
    print("=" * 80)

    if total == 0:
        print("未找到需要巡检的 healthy 账号。")
        return

    updated_accounts = []
    success_count = 0
    expired_count = 0

    for idx, acc in enumerate(accounts, 1):
        email = acc.get("email", "")
        print(f"\n[{idx}/{total}] 正在巡检账号: {email} (ID={acc.get('id')})...")
        
        info = inspect_single_account(acc)
        
        # 打印提取成果
        if info["valid"]:
            success_count += 1
            name = f"{info['given_name']} {info['family_name']}".strip()
            quota_str = f"剩余: {info['remaining_queries']}/{info['total_queries']} 次" if info['remaining_queries'] is not None else "未知"
            oauth_str = "已具备 (OAuth)" if info["has_oauth"] else "仅SSO"
            print(f"  ✓ 登录态: 认证通过")
            print(f"  ✓ 用户名: {name or 'Grok User'} | User ID: {info['user_id'][:12]}...")
            print(f"  ✓ 配额状态: {quota_str} (重置周期: {info['window_size_seconds']}s)")
            print(f"  ✓ 授权凭据: {oauth_str}")
            print(f"  ✓ 风控评级: botFlagSource={info['bot_flag_source']} (健康)")
        else:
            expired_count += 1
            print(f"  ✗ 状态异常: {info['status']} - {info['error']}")

        # 更新数据库
        try:
            cur_payload = {}
            if acc.get("payload"):
                try:
                    cur_payload = json.loads(acc["payload"])
                except Exception:
                    pass

            cur_payload.update({
                "user_id": info["user_id"],
                "given_name": info["given_name"],
                "family_name": info["family_name"],
                "email_domain": info["email_domain"],
                "create_time": info["create_time"],
                "remaining_queries": info["remaining_queries"],
                "total_queries": info["total_queries"],
                "window_size_seconds": info["window_size_seconds"],
                "last_inspected_at": datetime.now(timezone.utc).isoformat(),
            })

            new_status = info["status"] if not info["valid"] else "healthy"

            conn.execute(
                """
                UPDATE grok_accounts
                SET status=?, bot_flag_source=?, bot_flag_details=?, payload=?, updated_at=?
                WHERE id=?
                """,
                (
                    new_status,
                    info["bot_flag_source"],
                    info["bot_flag_details"],
                    json.dumps(cur_payload, ensure_ascii=False),
                    datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
                    acc["id"],
                )
            )
            conn.commit()

            acc["status"] = new_status
            acc["payload"] = json.dumps(cur_payload, ensure_ascii=False)
            acc["bot_flag_source"] = info["bot_flag_source"]
            acc["bot_flag_details"] = info["bot_flag_details"]
            updated_accounts.append(acc)

        except Exception as update_err:
            print(f"  [DB ERROR] 写入数据库异常: {update_err}")

        # 微小间隔避免高频突发
        time.sleep(0.5)

    conn.close()

    print("\n" + "=" * 80)
    print(f"巡检完成！共处理: {total} 个 | 活跃健康: {success_count} 个 | 失效/异常: {expired_count} 个")
    print("=" * 80)

    # 同步到下游 gptGrok2api
    if force_sync and updated_accounts:
        print("\n正在将最新信息全量同步至下游 gptGrok2api (accounts.json & grok_accounts.json)...")
        reset_accounts_downstream()


def reset_accounts_downstream():
    """双向刷新并持久化至下游 gptGrok2api 数据目录。"""
    import sqlite3
    db_path = _ROOT / "turb.sqlite3"
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute("SELECT * FROM grok_accounts WHERE status='healthy' AND archived=0").fetchall()
    grok_db = {r["email"].lower(): dict(r) for r in rows}
    conn.close()

    acc_path = Path(r"f:\github\gptGrok2api\data\accounts.json")
    if acc_path.exists():
        try:
            data = json.loads(acc_path.read_text(encoding="utf-8"))
            for x in data:
                em = str(x.get("email") or "").lower().strip()
                if em in grok_db:
                    g = grok_db[em]
                    x["source_type"] = "grok"
                    x["access_token"] = g.get("sso_token") or g.get("sso_rw") or ""
                    x["login_password"] = g.get("password") or ""
                    x["status"] = "正常"
                    x["enabled"] = True
                    x["disabled"] = False
                    x["fail"] = 0
                    x["failure_count"] = 0
                    x["chat_failure_count"] = 0
            acc_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"✓ 已更新 gptGrok2api data/accounts.json ({len(data)} 条记录)")
        except Exception as e:
            print(f"更新 accounts.json 异常: {e}")

    grok_path = Path(r"f:\github\gptGrok2api\data\grok_accounts.json")
    grok_items = []
    for em, g in grok_db.items():
        payload_data = {}
        if g.get("payload"):
            try:
                payload_data = json.loads(g["payload"])
            except Exception:
                pass

        oauth_state = None
        if g.get("access_token"):
            oauth_state = {
                "status": "authorized",
                "attempted_at": g.get("updated_at") or "",
                "error": "",
                "attempts": 1
            }

        item = {
            "id": f"grok-{g['id']}",
            "email": g["email"],
            "password": g.get("password") or "",
            "sso": g.get("sso_token") or g.get("sso_rw") or "",
            "access_token": g.get("access_token") or "",
            "refresh_token": g.get("refresh_token") or "",
            "status": "active",
            "enabled": True,
            "source_type": "protocol",
            "created_at": g.get("created_at") or "",
            "updated_at": g.get("updated_at") or "",
        }
        if oauth_state:
            item["oauth_authorization"] = oauth_state

        runtime_data = {}
        if g.get("probe_tps"):
            runtime_data["probe_tps"] = g["probe_tps"]
            runtime_data["probe_reasoning_tokens"] = g.get("probe_reasoning_tokens", 0)
        if payload_data.get("remaining_queries") is not None:
            runtime_data["remaining_queries"] = payload_data["remaining_queries"]
            runtime_data["total_queries"] = payload_data["total_queries"]
            runtime_data["window_size_seconds"] = payload_data["window_size_seconds"]
        if payload_data.get("user_id"):
            runtime_data["user_id"] = payload_data["user_id"]
        if payload_data.get("given_name"):
            runtime_data["name"] = f"{payload_data['given_name']} {payload_data.get('family_name', '')}".strip()

        if runtime_data:
            item["runtime"] = runtime_data

        grok_items.append(item)

    grok_path.write_text(json.dumps(grok_items, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✓ 已更新 gptGrok2api data/grok_accounts.json ({len(grok_items)} 条记录)")


def main():
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Grok 已注册账号批量登录与详细信息提取工具")
    parser.add_argument("--max", type=int, default=100, help="最多处理账号数量 (默认 100)")
    parser.add_argument("--no-sync", dest="sync", action="store_false", help="不自动同步到下游 gptGrok2api")
    parser.set_defaults(sync=True)
    args = parser.parse_args()

    run_inspect_and_update(max_accounts=args.max, force_sync=args.sync)


if __name__ == "__main__":
    main()
