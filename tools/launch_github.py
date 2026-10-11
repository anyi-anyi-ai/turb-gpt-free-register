# -*- coding: utf-8 -*-
"""
GitHub 人机协同自动注册终端交互工作台 (Python 原生防乱码启动器)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# 确保 Windows 终端 UTF-8 输出
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.github import (
    init_github_table,
    load_pending_accounts,
)
from interactive_github_register import run_interactive_pipeline


def print_banner():
    print("\n" + "=" * 70)
    print("   GitHub 有头人机协同自动注册工作台 (CloakBrowser 独立指纹)")
    print("=" * 70)
    print("  [1] 立即开始单号注册 (默认模式：1个账号 / 自动调度代理)")
    print("  [2] 连续批量注册模式 (自动连续推进，直到手动退出或邮箱耗尽)")
    print("  [3] 本机直连 / 全局梯子模式 (--no-proxy，适合电脑已开梯子)")
    print("  [4] 手动指定代理节点 (如 http://127.0.0.1:7890)")
    print("  [5] 指定特定邮箱注册测试")
    print("  [6] 查看系统状态与待注册邮箱池统计")
    print("  [0] 退出")
    print("=" * 70)


def show_status():
    init_github_table()
    import sqlite3
    pending = load_pending_accounts()
    with sqlite3.connect("turb.sqlite3") as conn:
        cur = conn.cursor()
        cur.execute("SELECT status, count(*) FROM github_accounts GROUP BY status")
        gh_stats = cur.fetchall()

    print("\n" + "─" * 60)
    print("📊 当前系统与邮箱池状态：")
    print(f"   • 待注册可用邮箱池: {len(pending)} 个")
    if pending:
        print(f"   • 下一个待注册邮箱: {pending[0].email}")
    print("   • GitHub 历史记录统计:")
    if gh_stats:
        for s, c in gh_stats:
            print(f"     - 状态 [{s}]: {c} 个")
    else:
        print("     - (暂无历史记录)")
    print("─" * 60)


def main():
    while True:
        print_banner()
        try:
            choice = input("👉 请输入选项序号 [1-6, 0]: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\n正在退出...")
            break

        if choice == "1":
            print("\n🚀 [模式 1] 正在启动单号人机协同注册...\n")
            run_interactive_pipeline(max_count=1)
            try:
                input("\n按回车键返回主菜单...")
            except Exception:
                pass

        elif choice == "2":
            print("\n🚀 [模式 2] 正在启动连续批量人机协同注册...\n")
            run_interactive_pipeline(max_count=0)
            try:
                input("\n按回车键返回主菜单...")
            except Exception:
                pass

        elif choice == "3":
            print("\n🚀 [模式 3] 正在启动本机直连 / 全局梯子模式...\n")
            run_interactive_pipeline(max_count=1, no_proxy=True)
            try:
                input("\n按回车键返回主菜单...")
            except Exception:
                pass

        elif choice == "4":
            try:
                p_in = input("\n请输入代理地址 (默认 http://127.0.0.1:7890): ").strip()
            except Exception:
                p_in = ""
            if not p_in:
                p_in = "http://127.0.0.1:7890"
            print(f"\n🚀 [模式 4] 正在使用代理 {p_in} 启动...\n")
            run_interactive_pipeline(max_count=1, explicit_proxy=p_in)
            try:
                input("\n按回车键返回主菜单...")
            except Exception:
                pass

        elif choice == "5":
            try:
                email_in = input("\n请输入特定 Outlook 邮箱: ").strip()
            except Exception:
                email_in = ""
            if not email_in:
                print("❌ 邮箱不能为空！")
            else:
                print(f"\n🚀 [模式 5] 正在为 {email_in} 启动注册...\n")
                run_interactive_pipeline(max_count=1, explicit_email=email_in)
            try:
                input("\n按回车键返回主菜单...")
            except Exception:
                pass

        elif choice == "6":
            show_status()
            try:
                input("\n按回车键返回主菜单...")
            except Exception:
                pass

        elif choice == "0":
            print("\n👋 正在退出工作台...")
            break
        else:
            print("\n⚠️ 无效输入，请输入 1-6 或 0")


if __name__ == "__main__":
    main()
