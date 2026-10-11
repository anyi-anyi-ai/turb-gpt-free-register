# -*- coding: utf-8 -*-
"""
UNED 学生账号与邮箱批量自动激活 CLI 脚本
用法:
    python run_uned_activate.py
    python run_uned_activate.py --limit 5
"""

import argparse
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding='utf-8')
except Exception:
    pass

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.uned.activator import batch_activate_all_accounts

def main():
    parser = argparse.ArgumentParser(description="UNED 学生账号密码批量自动激活工具")
    parser.add_argument("--limit", type=int, default=None, help="最大激活数量限制（默认全部）")
    parser.add_argument("--force", action="store_true", help="强制对已激活账号重新验证与同步")
    args = parser.parse_args()

    batch_activate_all_accounts(limit=args.limit, force_recheck=args.force)

if __name__ == "__main__":
    main()
