# -*- coding: utf-8 -*-
"""
一键修复与自动养号自愈脚本 (Account Self-Healing & Recovery)

功能：
1. 自动比对 gptGrok2api 与 turb.sqlite3 中的账号状态，筛选出所有「异常 / 401 失效 / 封控受阻」的账号。
2. 逐一启动专属隔离指纹浏览器（CloakBrowser 独立环境与固定指纹 Seed，严格同国粘性代理）。
3. 智能免登自愈（若免登态过期，自动通过 邮箱 + 密码 + 动态 TOTP 2FA 重新登录并捕获全新 AccessToken 与 StorageState）。
4. 拟人养号（自然问答交互、主页漫游、模拟人类停留）。
5. 自动写回 SQLite 并通过 HTTP API + 本地文件双通道无缝同步恢复 gptGrok2api（重置为「正常」并清空错误计数）。

用法：
    # 自动检测并修复所有异常账号
    python scripts/repair_accounts.py

    # 修复指定账号 ID
    python scripts/repair_accounts.py --ids 50 51 8

    # 全量强制养号与刷新所有账号
    python scripts/repair_accounts.py --all

    # 无头/有头切换（默认根据 .env WARMING_HEADLESS，推荐有头调试）
    python scripts/repair_accounts.py --headed
"""
import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# 项目根目录导入
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

# 加载 .env
from dotenv import load_dotenv
load_dotenv(ROOT_DIR / ".env", override=True)

from core import db
from core.account_warming import warm_single_account
from core.gptgrok2api_sync import (
    push_accounts_to_gptgrok2api,
    _extract_account_info,
    GPTGROK2API_DATA_FILE,
    GPTGROK2API_URL,
    GPTGROK2API_KEY,
)
import requests

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("repair_accounts")


def get_abnormal_account_emails() -> set[str]:
    """从 gptGrok2api 的数据文件和 HTTP 接口中扫描所有异常/失效账号的邮箱。"""
    abnormal_emails = set()

    # 1. 扫描 gptGrok2api 接口
    try:
        base_url = GPTGROK2API_URL.split("/api/")[0]
        resp = requests.get(
            f"{base_url}/api/accounts",
            headers={"Authorization": f"Bearer {GPTGROK2API_KEY}"},
            timeout=5,
        )
        if resp.status_code == 200:
            items = resp.json().get("items", [])
            for item in items:
                status = str(item.get("status") or "")
                disabled = bool(item.get("disabled"))
                err = item.get("last_error_status") or item.get("last_error_message") or item.get("error")
                if status == "异常" or disabled or err or status != "正常":
                    em = str(item.get("email") or "").strip().lower()
                    if em:
                        abnormal_emails.add(em)
    except Exception as exc:
        logger.debug("读取 gptGrok2api 接口异常 (将检查本地文件): %s", exc)

    # 2. 扫描本地 accounts.json
    try:
        if GPTGROK2API_DATA_FILE.exists():
            data = json.loads(GPTGROK2API_DATA_FILE.read_text(encoding="utf-8"))
            for item in data:
                status = str(item.get("status") or "")
                disabled = bool(item.get("disabled"))
                err = item.get("last_error_status") or item.get("last_error_message") or item.get("error")
                if status == "异常" or disabled or err or status != "正常":
                    em = str(item.get("email") or "").strip().lower()
                    if em:
                        abnormal_emails.add(em)
    except Exception as exc:
        logger.debug("读取 accounts.json 异常: %s", exc)

    return abnormal_emails


def find_target_accounts(
    account_ids: list[int] | None = None,
    repair_all: bool = False,
    id_range: tuple[int, int] | None = None,
    missing_storage_only: bool = False,
    limit: int | None = None,
) -> list[dict]:
    """根据入参筛选需要自愈恢复的账号。"""
    all_accounts = db.list_accounts(limit=2000, archived=None)
    targets = []

    if account_ids:
        id_set = set(account_ids)
        for acc in all_accounts:
            if int(acc.get("id") or 0) in id_set:
                targets.append(acc)
    elif id_range:
        start_id, end_id = id_range
        for acc in all_accounts:
            aid = int(acc.get("id") or 0)
            if start_id <= aid <= end_id:
                # 自动跳过已确认被官方封禁 (deactivated) 的账号
                if str(acc.get("live_check_status") or "") == "deactivated":
                    continue
                targets.append(acc)
    elif repair_all:
        targets = [
            acc for acc in all_accounts
            if acc.get("email") and str(acc.get("live_check_status") or "") != "deactivated"
        ]
    else:
        # 默认自愈模式：自动识别所有异常、过期或需要修复的账号，严格跳过已永久停用 (deactivated) 的死号
        abnormal_emails = get_abnormal_account_emails()
        if abnormal_emails:
            logger.info("在 gptGrok2api 中检测到 %d 个异常账号: %s", len(abnormal_emails), list(abnormal_emails)[:10])

        for acc in all_accounts:
            live_status = str(acc.get("live_check_status") or "").strip().lower()
            # 严格跳过已确认为官方停用的死号，避免无效重试
            if live_status == "deactivated":
                continue

            em = str(acc.get("email") or "").strip().lower()
            token = str(acc.get("access_token") or "").strip()
            is_expired = bool(acc.get("token_expired")) or live_status in ("token_expired", "expired", "failed", "unverified")
            live_ok = acc.get("live_check_ok")

            # 触发自愈条件：
            # 1. 在 gptGrok2api 中标记为异常/disabled
            # 2. 状态为 token_expired 或 failed
            # 3. 测活未通过 (live_ok is False)
            # 4. Token 为空或极短 (<500)
            needs_heal = (
                em in abnormal_emails
                or is_expired
                or live_ok is False
                or len(token) < 500
            )

            if needs_heal:
                targets.append(acc)

    if missing_storage_only:
        targets = [
            acc for acc in targets
            if not acc.get("storage_state") or not isinstance(acc.get("storage_state"), dict)
        ]

    failed_ids = set()
    failures_file = ROOT_DIR / "data" / "repair_failures.json"
    if failures_file.exists():
        try:
            fdata = json.loads(failures_file.read_text(encoding="utf-8"))
            failed_ids = {int(item.get("id")) for item in fdata if item.get("id")}
        except Exception:
            pass

    targets.sort(key=lambda x: (1 if int(x.get("id") or 0) in failed_ids else 0, int(x.get("id") or 0)))
    if limit and limit > 0:
        targets = targets[:limit]
    return targets


def main():
    parser = argparse.ArgumentParser(description="一键修复与自动养号自愈脚本 (Self-Healing & Auto-Recovery)")
    parser.add_argument("--auto-heal", action="store_true", help="全自动自愈模式（默认），自动筛选所有过期/异常账号静默执行，仅在报错时报警")
    parser.add_argument("--ids", nargs="+", type=int, help="指定要修复的账号 ID 列表，如 --ids 50 51")
    parser.add_argument("--range", nargs=2, type=int, metavar=("START", "END"), help="指定要处理的账号 ID 范围，如 --range 1 48")
    parser.add_argument("--missing-storage", action="store_true", help="仅处理尚未具备免登态 (无 storage_state) 的账号")
    parser.add_argument("--all", action="store_true", help="强制全量修复所有非封禁账号")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（推荐，过盾率更高）")
    parser.add_argument("--headless", action="store_true", help="强制无头静默运行")
    parser.add_argument("--dialogue", "--warm", dest="dialogue", action="store_true", help="是否执行拟人问答交互（默认 False，极速低流量自愈恢复；开启后执行多轮拟人问答）")
    parser.add_argument("--turns", type=int, default=None, help="拟人问答交互轮数（默认跟随配置 2 轮）")
    parser.add_argument("--limit", type=int, default=None, help="本轮最大处理账号数量")
    parser.add_argument("--max-consecutive-fails", type=int, default=15, help="连续失败达到此阈值时主动中断，防止网络全局故障时无谓消耗")
    args = parser.parse_args()

    headless = None
    if args.headed:
        headless = False
    elif args.headless:
        headless = True

    id_range = (args.range[0], args.range[1]) if args.range else None
    targets = find_target_accounts(
        account_ids=args.ids,
        repair_all=args.all,
        id_range=id_range,
        missing_storage_only=args.missing_storage,
        limit=args.limit,
    )
    if not targets:
        logger.info("🎉 当前没有检测到需要修复的异常账号！所有账号状态均良好。")
        return

    logger.info("=======================================================")
    logger.info("🚀 启动全自动自愈流水线 (Self-Healing Pipeline)")
    logger.info("待处理账号: %d 个 | 模式: %s | 窗口: %s", len(targets), "全自愈" if not args.ids else "指定ID", "有头(推荐)" if headless is False else "无头")
    logger.info("策略: 成功静默流转，发生报错集中归档至 data/repair_failures.json")
    logger.info("=======================================================")

    success_count = 0
    fail_count = 0
    consecutive_fails = 0
    failures = []
    failures_file = ROOT_DIR / "data" / "repair_failures.json"
    failures_file.parent.mkdir(parents=True, exist_ok=True)

    for idx, acc in enumerate(targets, 1):
        aid = int(acc.get("id") or 0)
        email = acc.get("email")
        logger.info("[%d/%d] 正在自愈账号: %s (ID: %d)...", idx, len(targets), email, aid)

        # 执行拟人养号与免登自愈
        res = warm_single_account(
            aid,
            trigger="auto_heal",
            headless=headless,
            enable_dialogue=args.dialogue,
            dialogue_turns=args.turns,
        )
        if res.get("ok"):
            action_desc = "已完成拟人问答" if args.dialogue else "极速自愈免登"
            logger.info("✅ [%d/%d] ID %d (%s) 自愈成功！已捕获全新 Token 并恢复在线 (%s)。", idx, len(targets), aid, email, action_desc)
            success_count += 1
            consecutive_fails = 0
        else:
            err = res.get("error")
            fail_count += 1
            consecutive_fails += 1
            failures.append({
                "id": aid,
                "email": email,
                "error": str(err),
                "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            })
            logger.error("❌ [ALERT 报错] ID %d (%s) 修复失败: %s", aid, email, err)

            if consecutive_fails >= args.max_consecutive_fails:
                logger.critical("🛑 [CRITICAL] 连续失败达到 %d 次，可能存在全局代理或 Cloudflare 盾级阻断，自愈流程主动熔断中止！", consecutive_fails)
                break

        # 账号间短暂停顿防并发风控
        if idx < len(targets):
            time.sleep(2.5)

    # 失败结果记录回写文件
    if failures:
        failures_file.write_text(json.dumps(failures, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.warning("⚠️ 存在 %d 个自愈失败账号，已统一沉淀记录至: %s", len(failures), failures_file)
    else:
        if failures_file.exists():
            failures_file.unlink(missing_ok=True)

    # 全量补推一次 gptGrok2api 保证最新同步
    logger.info("\n正在触发 gptGrok2api 状态全量回写与刷新...")
    try:
        from core.gptgrok2api_sync import sync_all_registered_accounts
        sync_res = sync_all_registered_accounts(force=True)
        logger.info("gptGrok2api 同步结果: %s", sync_res)
    except Exception as e:
        logger.warning("gptGrok2api 同步调用异常: %s", e)

    logger.info("=======================================================")
    logger.info("🏁 账号自愈流水线执行完毕！成功: %d, 失败: %d", success_count, fail_count)
    if failures:
        logger.error("🚨 报错账号清单及原因:")
        for f in failures:
            logger.error("  - ID: %d | Email: %s | 原因: %s", f["id"], f["email"], f["error"])
    else:
        logger.info("🎉 全部账号均自愈恢复成功，无任何异常！")
    logger.info("=======================================================")


if __name__ == "__main__":
    main()
