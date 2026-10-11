# -*- coding: utf-8 -*-
"""
GitHub 【有头人机协同】自动注册系统 CLI 交互入口。

已通过 core.github 领域包完成彻底解耦：
- core.github.credentials: 极客命名生成与自愈算法
- core.github.flow: 启发式多级选择器表单流与双通道人机感知机
- core.github.otp: GitHub Launch Code 验证码多协议监听与提取
- core.github.probe: 借鉴 GrokIQ 的初生质量自检探针 (Post-Registration Probe)
- core.github.db: 专属 SQLite 数据模型与导出器
- core.github.service: 高层业务会话编排
"""
from __future__ import annotations

import argparse
import logging
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

from config.proxy import pick_proxy, PROXY_POOL, DEFAULT_REGISTER_COUNTRY
from core.continent_proxy_router import check_proxy_tunnel_alive
from core.ip_quota_manager import ip_quota_manager
from core.outlook_client import OutlookAccount

from core.github import (
    init_github_table,
    load_pending_accounts,
    register_github_account_session,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("github.cli")


def allocate_proxy_with_check(
    country: str | None = None,
    explicit_proxy: str | None = None,
    no_proxy: bool = False,
) -> tuple[str | None, str]:
    """分配并通过健康度前置检测的代理出口 (带失败熔断记录与本地代理回退)。"""
    if no_proxy:
        return None, "本机直连 / 本机系统代理"

    if explicit_proxy:
        alive = check_proxy_tunnel_alive(explicit_proxy, target_host="github.com", timeout=3.5)
        if alive:
            return explicit_proxy, f"指定代理 [{explicit_proxy}] (已验证)"
        logger.warning(f"[代理自检] 指定代理 {explicit_proxy} 未连通，仍尝试使用")
        return explicit_proxy, f"指定代理 [{explicit_proxy}] (连通警告)"

    target_country = country or DEFAULT_REGISTER_COUNTRY or "US"

    # 1. 尝试从高质量池中调度 (尝试 2 次，单次 2.0s)
    for attempt in range(2):
        p = pick_proxy(country=target_country, for_registration=True)
        if p:
            if check_proxy_tunnel_alive(p, target_host="github.com", timeout=2.0):
                logger.info(f"🌐 [代理调度] 成功分配并连通节点: {p} (地区={target_country})")
                return p, f"池化住宅代理 [{target_country}]"
            else:
                logger.warning(f"[代理自检] 代理网关不可达: {p}，自动记录异常并轮换 ({attempt+1}/2)")
                try:
                    ip_quota_manager.record_proxy_failure(p, reason="github_tunnel_check_failed", country=target_country)
                except Exception:
                    pass

    # 2. 尝试本地代理兜底
    for local_p in PROXY_POOL:
        if local_p.startswith("http://127.0.0.1") or local_p.startswith("socks5h://127.0.0.1"):
            if check_proxy_tunnel_alive(local_p, target_host="github.com", timeout=2.0):
                logger.info(f"🌐 [代理调度] 远端住宅池波动，回退至本地代理: {local_p}")
                return local_p, f"本地代理入口 [{local_p}]"

    logger.warning("⚠️ 所有代理连通性检测未通过，使用直连模式推进")
    return None, "直连模式 (代理自检未通过)"


def run_interactive_pipeline(
    start_index: int = 1,
    max_count: int = 0,
    explicit_email: str | None = None,
    explicit_proxy: str | None = None,
    no_proxy: bool = False,
    country: str | None = None,
    headless: bool = False,
    email_file: str | None = None,
):
    """GitHub 批量人机协同流水线主入口。"""
    init_github_table()

    # 1. 加载待处理账号
    if explicit_email:
        accounts = [OutlookAccount(
            email=explicit_email,
            password="",
            client_id="",
            refresh_token="",
        )]
    else:
        accounts = load_pending_accounts(source_file=email_file)

    if not accounts:
        print("\n❌ 未找到可用的待注册 Outlook 邮箱！")
        print("   请在 WebUI 邮箱池导入 Outlook 素材，或在 '用于注册的邮箱.txt' 中补充格式：")
        print("   email----password----clientId----refreshToken\n")
        return

    total = len(accounts)
    print("\n" + "=" * 70)
    print("🚀 GitHub 【有头人机协同】自动注册系统")
    print(f"📌 待处理账号总数: {total} 个 | 起始序号: 第 {start_index} 个")
    print("🛡️ 驱动引擎: CloakBrowser 有头指纹浏览器 + GeoIP + 专属 Seed")
    print("🧩 架构分层: core.github 模块化解耦 + GrokIQ 初生质量探针")
    print("=" * 70 + "\n")

    processed = 0
    success_count = 0

    for idx, acc in enumerate(accounts, 1):
        if idx < start_index:
            continue
        if max_count > 0 and processed >= max_count:
            print(f"🏁 已达到指定注册数量 ({max_count} 个)，任务完成！")
            break

        processed += 1
        print("\n" + "═" * 70)
        print(f"📋 【{idx}/{total}】准备注册账号: {acc.email}")
        print("═" * 70)

        # 调度并验证代理
        proxy_url, proxy_desc = allocate_proxy_with_check(
            country=country,
            explicit_proxy=explicit_proxy,
            no_proxy=no_proxy,
        )

        # 调用领域服务执行单号注册
        res = register_github_account_session(
            account=acc,
            proxy_url=proxy_url,
            proxy_desc=proxy_desc,
            headless=headless,
        )

        status = res.get("status")
        if status == "success":
            success_count += 1
            quality = res.get("quality", "active")
            print(f"\n✨ 【成功】{acc.email} 注册完毕并入库！质量评级: {quality} | 当前累计成功: {success_count} 个")
        elif status == "already_registered":
            print(f"\n⏩ 【跳过】{acc.email} 已经在 GitHub 注册过")
        else:
            print(f"\n⚠️ 【未完成】{acc.email} 状态: {status}")

        if idx >= total:
            print("\n🎉 所有待处理账号已全部推进完毕！")
            break

        # 推进控制
        print("\n" + "─" * 70)
        print("⌨️ 【推进操作】:")
        print("   • 直接按【回车键 (Enter)】: 立即推进下一个账号")
        print("   • 输入【s】+ 回车: 跳过下一个账号")
        print("   • 输入【q】+ 回车: 退出程序")
        print("─" * 70)

        try:
            choice = input(">>> 请输入指令 (直接回车继续): ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = ""
        if choice == "q":
            print("👋 正在退出人机协同系统...")
            break
        elif choice == "s":
            print("⏩ 已跳过下一个账号")
            continue

    print(f"\n📊 统计摘要：本次处理 {processed} 个，成功注册 {success_count} 个。数据保存在 turb.sqlite3 与 accounts_github.txt。")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GitHub 有头人机协同自动注册系统 (基于 CloakBrowser)")
    parser.add_argument("--start", type=int, default=1, help="起始账号序号 (从1开始)")
    parser.add_argument("--count", type=int, default=1, help="注册数量 (默认1个，0表示连续处理直到邮箱耗尽)")
    parser.add_argument("--email", type=str, default=None, help="显式指定单个注册邮箱")
    parser.add_argument("--proxy", type=str, default=None, help="显式指定代理地址 (如 http://127.0.0.1:7890)")
    parser.add_argument("--no-proxy", action="store_true", help="强制关闭代理走本机直连/系统代理")
    parser.add_argument("--country", type=str, default=None, help="首选代理国家代码 (如 US, SG, JP)")
    parser.add_argument("--email-file", type=str, default=None, help="指定外部待注册邮箱文本文件路径")
    parser.add_argument("--headless", action="store_true", help="是否无头启动 (调试用，默认为有头模式)")
    parser.add_argument("--status", action="store_true", help="查看当前数据库与待注册邮箱池状态")

    args = parser.parse_args()

    if args.status:
        init_github_table()
        import sqlite3
        pending = load_pending_accounts()
        with sqlite3.connect("turb.sqlite3") as conn:
            cur = conn.cursor()
            cur.execute("SELECT status, count(*) FROM github_accounts GROUP BY status")
            gh_stats = cur.fetchall()
        print("\n" + "=" * 60)
        print("📊 GitHub 注册系统状态一览：")
        print(f"   • 待注册可用邮箱池: {len(pending)} 个")
        if pending:
            print(f"   • 下一个待注册邮箱: {pending[0].email}")
        print("   • GitHub 历史记录统计:")
        if gh_stats:
            for s, c in gh_stats:
                print(f"     - 状态 [{s}]: {c} 个")
        else:
            print("     - (暂无历史记录)")
        print("=" * 60 + "\n")
        sys.exit(0)

    run_interactive_pipeline(
        start_index=args.start,
        max_count=args.count,
        explicit_email=args.email,
        explicit_proxy=args.proxy,
        no_proxy=args.no_proxy,
        country=args.country,
        headless=args.headless,
        email_file=args.email_file,
    )
