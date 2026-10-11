# -*- coding: utf-8 -*-
"""存量账号批量设定与对齐专属 OpenAI 密码脚本。

用法：
    # 为指定账号设置专属密码
    python scripts/batch_set_passwords.py --ids 31

    # 扫描并为所有缺少专属密码的账号批量对齐密码
    python scripts/batch_set_passwords.py --missing-only

    # 全量更新已激活的存活账号
    python scripts/batch_set_passwords.py --active-only
"""
import argparse
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
from core.cloakbrowser_driver import build_cloak_driver
from core.fingerprint import resolve_account_fingerprint_seed
from core.openai_password_service import set_openai_account_password
from core.browser_data_saver import BrowserDataSaver

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("batch_set_passwords")


def find_target_accounts(
    account_ids: list[int] | None = None,
    missing_only: bool = False,
    active_only: bool = False,
) -> list[dict]:
    """根据过滤规则筛选待设密码的目标账号。"""
    rows = db.list_accounts()
    targets = []

    for r in rows:
        aid = int(r.get("id") or 0)
        email = str(r.get("email") or "").strip()
        if not email:
            continue

        if account_ids and aid not in account_ids:
            continue

        # 检查封号状态，已封禁账号跳过
        live_status = str(r.get("status") or "")
        warming_err = str(r.get("warming_error") or "")
        if live_status == "deactivated" or "deactivated" in warming_err.lower():
            continue

        has_reg_pwd = bool(str(r.get("registration_password") or "").strip())
        if missing_only and has_reg_pwd:
            continue

        if active_only:
            # 必须具有 storage_state 或近期自愈成功的存活账号
            has_storage = bool(r.get("storage_state"))
            if not has_storage:
                continue

        targets.append(r)

    return sorted(targets, key=lambda x: int(x.get("id") or 0))


def set_password_for_account(acc: dict, headless: bool = False) -> bool:
    """为单账号启动隔离指纹浏览器并设置专属密码。"""
    aid = int(acc.get("id") or 0)
    email = str(acc.get("email") or "")
    country = str(acc.get("country") or "US")
    storage_state = acc.get("storage_state")

    from core.continent_proxy_router import resolve_account_proxy_3tier
    proxy_url, target_country, tier = resolve_account_proxy_3tier(acc, check_alive=True)
    logger.info("[账号 #%d %s] 代理分配成功 [%s]: %s (%s)", aid, email, tier, proxy_url, target_country)

    fp_seed = resolve_account_fingerprint_seed(acc, email=email)
    driver = None
    data_saver = None

    try:
        driver, _ = build_cloak_driver(
            proxy=proxy_url,
            country=target_country,
            storage_state=storage_state if isinstance(storage_state, dict) else None,
            headless=headless,
            fingerprint_seed=fp_seed,
        )
        page = getattr(driver, "page", None)
        if not page:
            raise RuntimeError("未能初始化浏览器 page 实例")

        # 挂载深度省流拦截器
        data_saver = BrowserDataSaver(label=f"PwdSet-{email}")
        context = getattr(page, "context", None)
        if context:
            data_saver.install_playwright(context)
            logger.info("[账号 #%d %s] 已激活省流量拦截器", aid, email)

        # 执行密码设定
        new_pwd = set_openai_account_password(
            driver,
            email,
            account_id=aid,
            timeout=50,
        )

        if new_pwd:
            logger.info("🎉 [账号 #%d %s] 专属 OpenAI 密码设置成功: %s", aid, email, new_pwd)
            return True
        else:
            logger.error("❌ [账号 #%d %s] 专属密码设置失败", aid, email)
            return False
    except Exception as exc:
        logger.error("❌ [账号 #%d %s] 密码设置异常: %s", aid, email, exc)
        return False
    finally:
        if data_saver:
            try:
                data_saver.stop()
                stats = data_saver.stats()
                logger.info("[账号 #%d %s] 省流统计: 拦截 %d 个请求", aid, email, stats.get("blocked_count", 0))
            except Exception:
                pass
        if driver:
            try:
                driver.quit()
            except Exception:
                pass


def main():
    parser = argparse.ArgumentParser(description="存量账号批量设定与对齐专属 OpenAI 密码")
    parser.add_argument("--ids", nargs="+", type=int, help="指定账号 ID，如 --ids 31 32")
    parser.add_argument("--missing-only", action="store_true", help="仅处理尚未设置专属密码的账号")
    parser.add_argument("--active-only", action="store_true", help="仅处理已激活有免登态的账号")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口（推荐）")
    parser.add_argument("--headless", action="store_true", help="无头运行")
    args = parser.parse_args()

    headless = False if args.headed else (True if args.headless else False)

    targets = find_target_accounts(
        account_ids=args.ids,
        missing_only=args.missing_only,
        active_only=args.active_only,
    )

    if not targets:
        logger.info("🎉 没有找到符合条件的待设密码账号。")
        return

    logger.info("=======================================================")
    logger.info("🚀 开始执行专属密码批量对齐设定，待处理账号: %d 个", len(targets))
    logger.info("=======================================================")

    success = 0
    failed = 0

    for idx, acc in enumerate(targets, 1):
        aid = int(acc.get("id") or 0)
        email = str(acc.get("email") or "")
        logger.info("\n[%d/%d] 正在处理账号: %s (ID: %d)...", idx, len(targets), email, aid)

        ok = set_password_for_account(acc, headless=headless)
        if ok:
            success += 1
        else:
            failed += 1

        if idx < len(targets):
            time.sleep(3.0)

    # 同步一次 gptGrok2api
    try:
        from core.gptgrok2api_sync import sync_all_registered_accounts
        sync_all_registered_accounts(force=True)
    except Exception:
        pass

    logger.info("\n=======================================================")
    logger.info("🏁 密码对齐任务执行完毕！成功: %d, 失败: %d", success, failed)
    logger.info("=======================================================")


if __name__ == "__main__":
    main()
