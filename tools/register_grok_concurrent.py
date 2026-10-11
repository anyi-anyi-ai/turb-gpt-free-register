# -*- coding: utf-8 -*-
"""Grok 多国纯净节点并发注册工具。

基于 CloakBrowser 指纹浏览器与 GrokRegistrationManager 调度器，
支持多 Worker 线程池并发、多国纯净节点动态轮换、自动收取验证码、
OAuth凭据交换与初生自检，并自动同步至下游 gptGrok2api。
"""
import argparse
import sys
import time
from pathlib import Path

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.grok.registration_service import grok_reg_manager
from config.proxy import get_available_countries

DEFAULT_ROTATION_COUNTRIES = ["US", "CA", "DE", "FR", "NL", "JP", "SG"]


def main():
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Grok 多国纯净节点并发注册工具")
    parser.add_argument("--count", type=int, default=4, help="总计划注册账号数量 (默认 4)")
    parser.add_argument("--concurrency", "-c", type=int, default=2, help="并发 Worker 线程数 (默认 2，推荐 2-3)")
    parser.add_argument("--countries", nargs="+", default=None, help="允许轮换的国家列表 (默认 US CA DE FR NL JP SG)")
    parser.add_argument("--country", default=None, help="指定固定单国家代码 (例如 CA, US)")
    parser.add_argument("--proxy", default=None, help="可选显式出口代理 (留空则按国家自动分配 Resin 纯净节点)")
    parser.add_argument("--headless", action="store_true", default=None, help="启用无头模式 (默认读取 .env CLOAK_HEADLESS)")
    parser.add_argument("--no-probe", action="store_true", help="跳过第二级 Prompt 探针自检")
    parser.add_argument("--no-oauth", action="store_true", help="跳过 Device OAuth 凭据交换")
    parser.add_argument("--no-sync", dest="sync", action="store_false", help="不自动同步到下游 gptGrok2api")
    parser.set_defaults(sync=True)
    args = parser.parse_args()

    # 确定轮换国家列表
    if args.country:
        rotation_countries = [args.country.strip().upper()]
    elif args.countries:
        rotation_countries = [c.strip().upper() for c in args.countries if c.strip()]
    else:
        configured = get_available_countries()
        rotation_countries = [c for c in DEFAULT_ROTATION_COUNTRIES if c in configured] or ["US"]

    total_count = max(1, args.count)
    concurrency = max(1, min(args.concurrency, 10, total_count))

    print("=" * 70)
    print("★ Grok 多国纯净节点并发自动化注册中心 ★")
    print(f"  计划注册总数: {total_count} 个")
    print(f"  并发 Worker 数: {concurrency} 个")
    print(f"  国家轮换池:   {', '.join(rotation_countries)}")
    print(f"  代理出口分配: {'指定: ' + args.proxy if args.proxy else '自动按国家分配 Resin 纯净粘性出口'}")
    print(f"  初生探针自检: {'关闭' if args.no_probe else '开启'}")
    print(f"  Device OAuth: {'关闭' if args.no_oauth else '开启'}")
    print(f"  下游服务同步: {'开启 (gptGrok2api)' if args.sync else '关闭'}")
    print(f"  浏览器界面:   {'无头 (Headless)' if args.headless else '有界面 (GUI)' if args.headless is not None else '遵循环境配置'}")
    print("=" * 70)

    # 注册实时控制台日志监听
    def _console_log_listener(msg: str):
        print(msg, flush=True)

    grok_reg_manager.add_log_listener(_console_log_listener)

    start_time = time.time()
    started = grok_reg_manager.start_batch(
        count=total_count,
        concurrency=concurrency,
        proxy=args.proxy or "",
        countries=rotation_countries,
        sync_to_downstream=args.sync,
        run_quality_probe=not args.no_probe,
        enable_device_oauth=not args.no_oauth,
        headless=args.headless,
    )

    if not started:
        print("[ERROR] 无法启动任务：已有 Grok 批量注册任务正在执行中！")
        sys.exit(1)

    try:
        while grok_reg_manager.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[WARN] 接收到中断信号 (Ctrl+C)，正在安全停止所有注册 Worker...")
        grok_reg_manager.stop()
        while grok_reg_manager.is_running:
            time.sleep(0.5)

    grok_reg_manager.remove_log_listener(_console_log_listener)

    duration = time.time() - start_time
    status = grok_reg_manager.get_status()
    stats = status.get("stats", {})
    results = status.get("results", [])

    print("\n" + "=" * 70)
    print("★ Grok 并发注册任务最终汇总报告 ★")
    print(f"  耗时: {duration:.1f} 秒 | 计划: {stats.get('total', 0)} | 成功: {stats.get('success', 0)} | 降智: {stats.get('degraded', 0)} | 失败: {stats.get('failed', 0)}")
    print("-" * 70)

    for i, res in enumerate(results, 1):
        email = res.get("email") or "N/A"
        country = res.get("target_country") or "?"
        acc_status = res.get("status") or ("FAILED" if not res.get("success") else "OK")
        sync_info = "同步成功" if res.get("sync_result", {}).get("ok") else ("同步跳过" if not args.sync else f"未同步 ({res.get('sync_error', 'N/A')})")
        print(f"  {i}. [{country}] {email:30s} | 状态: {acc_status:12s} | 下游: {sync_info}")

    print("=" * 70)


if __name__ == "__main__":
    main()
