# -*- coding: utf-8 -*-
"""
run_suno_register.py — Suno 自动化注册 CLI (向下兼容垫片)
推荐直接使用统一调度中枢命令：
    python turb.py run suno [--count N] [--email xxx] [--headful]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.plugins.registry import PluginRegistry


def main():
    parser = argparse.ArgumentParser(description="Suno 注册 CLI (向下兼容垫片)")
    parser.add_argument("--count", type=int, default=1, help="注册账号数量")
    parser.add_argument("--email", type=str, default=None, help="指定邮箱")
    parser.add_argument("--no-headless", action="store_true", help="开启可视化窗口")
    parser.add_argument("--list", action="store_true", help="查看已注册账号")
    parser.add_argument("--sync-all", action="store_true", help="补推所有账号")
    args = parser.parse_args()

    print("[Turb 兼容层] 提示: 推荐使用新版统一中枢命令: `python turb.py run suno`")

    if args.list:
        from turb import handle_accounts
        dummy_args = argparse.Namespace(platform="suno", limit=50)
        handle_accounts(dummy_args)
        return

    from turb import handle_run
    dummy_args = argparse.Namespace(
        platform="suno",
        count=args.count,
        email=args.email,
        proxy=None,
        headful=args.no_headless,
        headless=not args.no_headless,
        no_push=False,
    )
    handle_run(dummy_args)


if __name__ == "__main__":
    main()
