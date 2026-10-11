# -*- coding: utf-8 -*-
"""
UNED 自动化注册标准 CLI 命令行工具。
完全基于独立的 core.uned 组件，支持单账号注册与全自动批量无缝挂机注册。

用法示例：
1. 注册 1 个账号（默认带窗口可视化）：
   python run_uned_register.py

2. 批量全自动挂机注册 5 个账号（静默后台无头模式）：
   python run_uned_register.py --count 5 --headless --interval 5

3. 指定代理出口注册：
   python run_uned_register.py --proxy "http://127.0.0.1:2260"
"""

import argparse
import sys
from pathlib import Path

# 适配 Windows 控制台 UTF-8 输出
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 确保项目根目录在 sys.path
_PROJECT_ROOT = Path(__file__).resolve().parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from core.uned import UnedRegistrar


def main():
    parser = argparse.ArgumentParser(
        description="UNED (西班牙国立远程教育大学) 全自动批量无缝注册工具",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--count", "-n", type=int, default=1, help="注册目标数量 (默认: 1)")
    parser.add_argument("--headless", action="store_true", help="无头模式（后台静默运行，适合批量挂机）")
    parser.add_argument("--interval", type=int, default=5, help="批量注册各账号之间的间隔秒数 (默认: 5)")
    parser.add_argument("--proxy", type=str, default=None, help="自定义代理 URL (留空默认使用项目 Resin 粘性代理池)")
    parser.add_argument("--debug", action="store_true", help="输出更详细的调试日志")

    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("      UNED 标识符全自动注册系统 (Automated SOID)        ")
    print("=" * 60)
    print(f" 目标数量:   {args.count} 个")
    print(f" 运行模式:   {'静默后台 (Headless)' if args.headless else '可视化前台 (Headed)'}")
    print(f" 批次间隔:   {args.interval} 秒")
    print(f" 代理模式:   {args.proxy or '自动粘性代理池 (Resin)'}")
    print(f" 验证码识别: 本地 RapidOCR ONNX 自动识别 + 刷新重试")
    print(f" 邮箱渠道:   Microsoft Graph API 直连收取验证码")
    print("=" * 60 + "\n")

    registrar = UnedRegistrar(debug=args.debug)

    if args.count <= 1:
        result = registrar.register_one(
            headless=args.headless,
            proxy_url=args.proxy
        )
        if result.success:
            print("\n" + "=" * 60)
            print("                 ★ UNED 注册成功 ★                   ")
            print("=" * 60)
            print(f"用户标识符: {result.uned_identifier}")
            print(f"分配学生邮箱: {result.student_email}")
            print(f"绑定邮箱: {result.email}")
            print(f"账户密码: {result.password}")
            print(f"绑定手机: {result.phone}")
            print(f"证件号码: {result.doc_number}")
            print(f"持有姓名: {result.full_name}")
            print("数据落盘: turb.sqlite3 (uned_accounts) & data/uned_accounts.json")
            print("=" * 60 + "\n")
            sys.exit(0)
        else:
            print(f"\n[失败] 注册未能成功完成: {result.error}\n")
            sys.exit(1)
    else:
        results = registrar.batch_register(
            count=args.count,
            headless=args.headless,
            interval_seconds=args.interval
        )
        succ = [r for r in results if r.success]
        print("\n" + "=" * 60)
        print("                 ★ 批量注册汇总汇报 ★                 ")
        print("=" * 60)
        print(f"总计请求: {len(results)} | 成功: {len(succ)} | 失败: {len(results) - len(succ)}")
        print("-" * 60)
        for idx, r in enumerate(succ, 1):
            print(f"[{idx}] ID: {r.uned_identifier:<12} | 学生邮箱: {r.student_email:<26} | 绑定: {r.email}")
        print("=" * 60 + "\n")
        if not succ:
            sys.exit(1)


if __name__ == "__main__":
    main()
