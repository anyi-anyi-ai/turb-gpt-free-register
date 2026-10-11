# -*- coding: utf-8 -*-
"""
Proxy Auto-Harvester (100GB / 1,000 节点纯净代理全自动获取与 Resin 热更调度器)

用法示例:
    # 1. 默认生产模式：获取 10 个账号 (1,000 节点 = 100GB) 并自动全量入池 Resin
    python run_proxy_harvester.py

    # 2. 快速测试模式：仅注册 1 个账号 (100 节点) 并展示可视化浏览器窗口
    python run_proxy_harvester.py --accounts 1 --headed

    # 3. 指定邮箱源 (如强制使用西班牙 UNED 邮箱或 CF 域名邮箱)
    python run_proxy_harvester.py --email-source uned

    # 4. 查看历史采集批次与账本
    python run_proxy_harvester.py --list
"""
from __future__ import annotations

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# 确保控制台 UTF-8 输出
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.proxy_harvester import HarvesterController
from core.proxy_harvester.models import DEFAULT_DB_PATH, init_harvester_db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("harvester.cli")


def list_batches(db_path: Path | str | None = None) -> None:
    """列出历史采集的批次信息与节点总数。"""
    p = Path(db_path or DEFAULT_DB_PATH)
    init_harvester_db(p)

    with sqlite3.connect(p) as conn:
        rows = conn.execute("""
            SELECT id, batch_id, total_accounts, total_nodes, file_path, synced_to_resin, created_at
            FROM harvested_proxy_batches
            ORDER BY id DESC LIMIT 15;
        """).fetchall()

    print("\n" + "=" * 90)
    print(f"{'ID':<4} {'批次标识':<18} {'账号数':<8} {'节点数':<10} {'Resin同步':<10} {'生成时间':<20} {'文件路径'}")
    print("-" * 90)
    if not rows:
        print("暂无已采集的代理批次记录。")
    for r in rows:
        synced = "🟢 已入池" if r[5] == 1 else "⚪ 未同步"
        print(f"{r[0]:<4} {r[1]:<18} {r[2]:<8} {r[3]:<10} {synced:<10} {r[6]:<20} {r[4]}")
    print("=" * 90 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Proxy Auto-Harvester: 100GB / 1000 节点纯净代理自动化获取与 Resin 入池调度系统"
    )
    parser.add_argument(
        "--accounts", "-n",
        type=int,
        default=10,
        help="目标获取的独立账号数 (每账号 100 节点，默认 10 账号 = 1,000 节点)",
    )
    parser.add_argument(
        "--email-source",
        choices=["auto", "outlook", "uned", "cf_custom"],
        default="auto",
        help="邮箱调度来源：auto(级联: Outlook->UNED->CF) | outlook | uned | cf_custom",
    )
    parser.add_argument(
        "--provider",
        default="auto",
        choices=["auto", "webshare", "plainproxies", "proxyscrape"],
        help="代理商驱动：auto(多代理商自动轮询) | webshare | plainproxies | proxyscrape",
    )
    parser.add_argument(
        "--platform",
        default="FAST_UPSTREAM",
        help="上游 Resin 注册出口平台 (默认: FAST_UPSTREAM，可选: DE, JP, Americas 等)",
    )
    parser.add_argument(
        "--headed",
        action="store_true",
        help="开启有头可视化浏览器窗口 (默认无头静默运行)",
    )
    parser.add_argument(
        "--no-sync",
        action="store_true",
        help="采集完成后不自动触发 Resin sync_custom_proxies.py 流水线",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="列出本地所有已采集批次历史并退出",
    )

    args = parser.parse_args()

    if args.list:
        list_batches()
        return

    controller = HarvesterController(
        target_accounts=args.accounts,
        provider=args.provider,
        headless=not args.headed,
        preferred_email=args.email_source,
        resin_platform=args.platform,
        auto_sync_resin=not args.no_sync,
    )

    result = controller.run_harvest()
    if not result.get("success"):
        sys.exit(1)


if __name__ == "__main__":
    main()
