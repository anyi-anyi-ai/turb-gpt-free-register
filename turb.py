# -*- coding: utf-8 -*-
"""
turb.py — 全平台模块化插件注册中枢与统一 CLI 调度门面 (Unified Registration Hub)

面向人类与 AI Agent 的最高指挥入口，彻底消除碎片脚本与重复代码阅读：
  1. 查看所有支持平台能力清单:
     python turb.py list
     python turb.py list --json      (专供 Agent 极速自省)

  2. 执行注册任务 (所有网站语法统一):
     python turb.py run suno --count 1
     python turb.py run elevenlabs --count 1
     python turb.py run udio --email "target@outlook.com"
     python turb.py run muse --headful

  3. 查看指定平台已注册资产:
     python turb.py accounts suno

  4. 系统环境与门禁自检:
     python turb.py preflight
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, List, Optional

# 设置标准编码
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.plugins.registry import PluginRegistry
from core.kits import KitBundle

# 配置控制台日志
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("turb.hub")


def handle_list(args: argparse.Namespace) -> None:
    """处理 list 命令：输出支持的插件矩阵。"""
    manifest = PluginRegistry.export_manifest()

    if getattr(args, "json", False):
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return

    plugins = manifest["plugins"]
    kits = KitBundle.create()
    stats = kits.email.get_pool_stats()

    print("\n" + "=" * 90)
    print(f"🚀 Turb 全平台插件化注册中枢 (v{manifest['version']})")
    print(f"📦 邮箱池就绪状态: 活跃可用邮箱 {stats['total_active_emails']} 个")
    print("=" * 90)
    print(f"{'平台 ID':<14} {'平台名称':<22} {'驱动':<10} {'人工拼图':<10} {'数据库表名':<22}")
    print("-" * 90)
    for p in plugins:
        human_str = "需要(自动弹窗)" if p["requires_human"] else "全自动"
        print(f"{p['id']:<14} {p['name']:<22} {p['browser_type']:<10} {human_str:<10} {p['table_name']:<22}")
    print("=" * 90)
    print("💡 调度语法: python turb.py run <平台 ID> [--count N] [--email xxx] [--headful]")
    print("=" * 90 + "\n")


def handle_run(args: argparse.Namespace) -> None:
    """处理 run 命令：执行注册流水线。"""
    from core.agent_bridge import stream_broadcaster

    is_stream = getattr(args, "stream", False)
    if is_stream:
        stream_broadcaster.enable_streaming(True)
        # 流式模式下静默普通日志，避免污染标准输出的 NDJSON 事件
        logging.getLogger().setLevel(logging.WARNING)

    plugin_id = args.platform.lower().strip()
    plugin_cls = PluginRegistry.get(plugin_id)

    if not plugin_cls:
        if is_stream:
            stream_broadcaster.emit_error(
                error_code="UNKNOWN_PLATFORM",
                message=f"未知平台: '{plugin_id}'",
                recovery_action="check_platform_list",
                platform=plugin_id,
            )
        else:
            print(f"\n[!] 未知平台: '{plugin_id}'")
            print(f"[*] 可用平台列表: {', '.join([p['id'] for p in PluginRegistry.list_all()])}")
            print("[*] 运行 `python turb.py list` 查看完整平台清单。\n")
        sys.exit(1)

    plugin = plugin_cls()
    if getattr(args, "country", None):
        plugin.target_country = args.country

    count = max(1, args.count)
    headless = False if args.headful else (True if args.headless else None)
    auto_push = not args.no_push

    if not is_stream:
        print("\n" + "=" * 80)
        print(f"🎯 启动注册任务: 目标平台 [{plugin.name}] ({plugin.id})")
        print(f"📊 计划注册数量: {count} 个 | 出口国家: {getattr(plugin, 'target_country', 'US')} | 模式: {getattr(plugin, 'tier', 'heavy_browser')}")
        if args.email:
            print(f"📧 指定邮箱: {args.email}")
        if plugin.requires_human:
            print("🧩 [提示] 该平台需要人机协作拼图，脚本已自动挂载可视化窗口并实时监听！")
        print("=" * 80 + "\n")
    else:
        stream_broadcaster.emit(
            "run_started",
            platform=plugin.id,
            count=count,
            tier=getattr(plugin, "tier", "heavy_browser"),
            country=getattr(plugin, "target_country", "US"),
        )

    success_cnt = 0
    fail_cnt = 0

    for i in range(1, count + 1):
        if count > 1 and not is_stream:
            print(f"\n--- 正在执行第 {i}/{count} 个账号注册 ---")

        res = plugin.run(
            specific_email=args.email if i == 1 else None,
            proxy=args.proxy,
            headless=headless,
            auto_push=auto_push,
        )

        if res.ok:
            success_cnt += 1
            if not is_stream:
                print(f"[✓] 成功完成: {res.email} (Status: {res.status}) [Vault: {res.metadata.get('vault_id', 'N/A')}]")
        else:
            fail_cnt += 1
            if not is_stream:
                print(f"[✗] 注册失败: {res.email} - 原因: {res.error}")

        if i < count:
            time.sleep(2.0)

    if not is_stream:
        print("\n" + "=" * 80)
        print(f"🏁 任务执行完毕 | 成功: {success_cnt} | 失败: {fail_cnt} | 总计: {count}")
        print("=" * 80 + "\n")
    else:
        stream_broadcaster.emit(
            "run_completed",
            platform=plugin.id,
            total=count,
            success=success_cnt,
            failed=fail_cnt,
        )


def handle_vault(args: argparse.Namespace) -> None:
    """处理 vault 命令：查看与还原身份资产档案。"""
    from core.vault.identity_vault import get_identity_vault

    vault = get_identity_vault()
    action = getattr(args, "vault_action", "list")

    if action == "list":
        identities = vault.list_identities(
            platform=getattr(args, "platform", None),
            country=getattr(args, "country", None),
        )
        if getattr(args, "json", False):
            print(json.dumps([i.to_dict() for i in identities], ensure_ascii=False, indent=2))
            return

        print("\n" + "=" * 90)
        print(f"🔐 统一身份档案库 (Identity Vault) — 已固化 {len(identities)} 条全生命周期资产")
        print("-" * 90)
        print(f"{'Vault ID':<32} {'平台':<12} {'账号/邮箱':<28} {'出口国家':<8} {'驱动':<10}")
        print("-" * 90)
        if not identities:
            print("暂无已固化的身份档案。")
        for i in identities:
            print(f"{i.vault_id:<32} {i.platform:<12} {i.account_id:<28} {i.target_country:<8} {i.driver_type:<10}")
        print("=" * 90 + "\n")

    elif action == "inspect":
        vault_id = args.vault_id
        snapshot = vault.get_identity(vault_id)
        if not snapshot:
            print(f"[!] 未找到指定身份档案: {vault_id}")
            sys.exit(1)
        print(json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2))

    elif action == "restore":
        vault_id = args.vault_id
        try:
            ctx = vault.restore_session_context(vault_id)
            print(f"\n[✓] 成功还原身份环境 ({vault_id}):")
            print(f"    - 平台: {ctx['platform']}")
            print(f"    - 账号: {ctx['account_id']}")
            print(f"    - 出口国家一致性保障: {ctx['target_country']}")
            print(f"    - 分配代理出口: {ctx['proxy']}")
            print(f"    - 硬件指纹 Seed: {ctx['fingerprint_seed']}")
            print(f"    - 载入 Cookies 数: {len(ctx.get('cookies') or [])} 条\n")
        except Exception as e:
            print(f"[✗] 还原身份环境异常: {e}")
            sys.exit(1)



def handle_accounts(args: argparse.Namespace) -> None:
    """查看指定平台的账号列表。"""
    plugin_id = args.platform.lower().strip()
    plugin_cls = PluginRegistry.get(plugin_id)

    if not plugin_cls:
        print(f"[!] 未知平台: {plugin_id}")
        sys.exit(1)

    plugin = plugin_cls()
    accounts = plugin.kits.asset.list_accounts(plugin.table_name, limit=args.limit)

    print("\n" + "=" * 80)
    print(f"📁 平台 [{plugin.name}] 已注册账号资产 (最近 {len(accounts)} 条)")
    print("-" * 80)
    print(f"{'ID':<4} {'邮箱':<35} {'API Key / Token':<18} {'状态':<8} {'网关同步':<8} {'更新时间'}")
    print("-" * 80)
    if not accounts:
        print("暂无已持久化的账号记录。")
    for r in accounts:
        synced_str = "已同步" if r.get("synced_to_gateway") == 1 else "未同步"
        key_str = (r.get("api_key") or "")[:15] + "..." if r.get("api_key") else "Session"
        r_id = str(r.get("id") or "")
        r_email = str(r.get("email") or "")
        r_status = str(r.get("status") or "")
        r_updated = str(r.get("updated_at") or "")
        print(f"{r_id:<4} {r_email:<35} {key_str:<18} {r_status:<8} {synced_str:<8} {r_updated}")
    print("=" * 80 + "\n")


def handle_preflight(args: argparse.Namespace) -> None:
    """环境与网络健康前置门禁自检。"""
    print("\n" + "=" * 80)
    print("🔍 Turb 系统运行时环境与前置门禁自检")
    print("=" * 80)

    # 1. 数据库检查
    try:
        from core.db.base import _sqlite_conn
        with _sqlite_conn() as conn:
            cnt = conn.execute("SELECT COUNT(1) FROM email_pool WHERE status IN ('available', 'active');").fetchone()[0]
            print(f"[✓] 本地 SQLite 存储: 正常 (邮箱池活跃可用数: {cnt})")
    except Exception as e:
        print(f"[✗] 本地 SQLite 异常: {e}")

    # 2. 浏览器检查
    try:
        from core.cloakbrowser_driver import build_cloak_driver
        print("[✓] CloakBrowser 驱动环境: 正常可用")
    except Exception as e:
        print(f"[!] CloakBrowser 加载警告: {e}")

    # 3. 插件装载检查
    plugins = PluginRegistry.list_all()
    print(f"[✓] 插件注册中心: 已成功就绪 {len(plugins)} 个注册插件 ({', '.join([p['id'] for p in plugins])})")
    print("=" * 80 + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="python turb.py",
        description="Turb 全平台插件化注册中枢与统一 CLI 调度器",
    )
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # list
    p_list = subparsers.add_parser("list", help="列出所有可用的网站注册插件")
    p_list.add_argument("--json", action="store_true", help="输出机器可读的 JSON 元数据清单 (供 Agent 自省)")

    # run
    p_run = subparsers.add_parser("run", help="执行指定平台的统一注册流程")
    p_run.add_argument("platform", type=str, help="目标平台 ID (例如 suno, elevenlabs, udio, muse 等)")
    p_run.add_argument("--count", type=int, default=1, help="注册账号数量 (默认 1)")
    p_run.add_argument("--email", type=str, default=None, help="指定领取的特定邮箱")
    p_run.add_argument("--proxy", type=str, default=None, help="指定代理节点")
    p_run.add_argument("--stream", action="store_true", help="启用 NDJSON 逐行流式事件输出 (专供 Agent 实时监听)")
    p_run.add_argument("--country", type=str, default=None, help="指定或覆盖目标出口地区/国家 (例如 US, DE, ES)")
    p_run.add_argument("--headful", action="store_true", help="强制以可视化有头窗口运行")
    p_run.add_argument("--headless", action="store_true", help="强制以无头模式运行")
    p_run.add_argument("--no-push", action="store_true", help="跳过下游网关同步")

    # vault (统一身份档案库)
    p_vault = subparsers.add_parser("vault", help="查看与还原统一身份档案 (Identity Vault)")
    p_vault_subs = p_vault.add_subparsers(dest="vault_action", help="身份档案操作")

    p_v_list = p_vault_subs.add_parser("list", help="查看已固化的身份档案列表")
    p_v_list.add_argument("--platform", type=str, default=None, help="按平台筛选")
    p_v_list.add_argument("--country", type=str, default=None, help="按出口国家筛选")
    p_v_list.add_argument("--json", action="store_true", help="输出完整 JSON 数组")

    p_v_inspect = p_vault_subs.add_parser("inspect", help="查看单条身份档案详情")
    p_v_inspect.add_argument("vault_id", type=str, help="档案 ID")

    p_v_restore = p_vault_subs.add_parser("restore", help="还原指定账号的同国家 IP 与硬件指纹上下文")
    p_v_restore.add_argument("vault_id", type=str, help="档案 ID")

    # accounts
    p_acc = subparsers.add_parser("accounts", help="查看指定平台的已注册账号列表")
    p_acc.add_argument("platform", type=str, help="目标平台 ID")
    p_acc.add_argument("--limit", type=int, default=30, help="显示数量限制")

    # preflight
    subparsers.add_parser("preflight", help="环境与网络健康前置门禁自检")

    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(0)

    args = parser.parse_args()

    if args.command == "list":
        handle_list(args)
    elif args.command == "run":
        handle_run(args)
    elif args.command == "vault":
        handle_vault(args)
    elif args.command == "accounts":
        handle_accounts(args)
    elif args.command == "preflight":
        handle_preflight(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
