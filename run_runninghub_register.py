# -*- coding: utf-8 -*-
"""
run_runninghub_register.py — RunningHub 自动化注册 CLI (向下兼容垫片)
推荐直接使用统一调度中枢命令：
    python turb.py run runninghub [--count N] [--email xxx]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def main():
    parser = argparse.ArgumentParser(description="RunningHub 注册 CLI (向下兼容垫片)")
    parser.add_argument("--count", type=int, default=1, help="注册账号数量")
    parser.add_argument("--email", type=str, default=None, help="指定邮箱")
    parser.add_argument("--list", action="store_true", help="查看已注册账号")
    args = parser.parse_args()

    print("[Turb 兼容层] 提示: 推荐使用新版统一中枢命令: `python turb.py run runninghub`")

    if args.list:
        from turb import handle_accounts
        dummy_args = argparse.Namespace(platform="runninghub", limit=50)
        handle_accounts(dummy_args)
        return

    from turb import handle_run
    dummy_args = argparse.Namespace(
        platform="runninghub",
        count=args.count,
        email=args.email,
        proxy=None,
        headful=False,
        headless=True,
        no_push=False,
    )
    handle_run(dummy_args)


if __name__ == "__main__":
    main()
