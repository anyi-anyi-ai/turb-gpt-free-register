# -*- coding: utf-8 -*-
"""Grok 多国纯净节点轮换注册与自检工具。"""
import argparse
import sys
import time
from pathlib import Path

# 确保项目根目录在 sys.path
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.grok.registration_service import register_single_grok_account
from config.proxy import pick_proxy, get_available_countries

DEFAULT_ROTATION_COUNTRIES = ["US", "CA", "DE", "FR", "NL", "JP", "SG"]


def main():
    if sys.platform == "win32":
        import io
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Grok 多国纯净节点轮换注册工具")
    parser.add_argument("--count", type=int, default=1, help="注册账号数量 (默认 1)")
    parser.add_argument("--country", default=None, help="指定固定注册国家代码 (例如 US, CA, DE)，留空则在多国纯净池中自动轮换")
    parser.add_argument("--countries", nargs="+", default=None, help="允许轮换的国家列表 (默认 US CA DE FR NL JP SG)")
    parser.add_argument("--proxy", default=None, help="可选显式出口代理 (留空则按国家自动选用 Resin 纯净节点池)")
    parser.add_argument("--no-probe", action="store_true", help="跳过第二级 Prompt 探针自检")
    parser.add_argument("--max-retries", type=int, default=15, help="遇到已注册邮箱时的最大顺延重试次数 (默认 15)")
    parser.add_argument("--headless", action="store_true", default=None, help="启用无头模式 (默认读取 .env CLOAK_HEADLESS)")
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

    print("=" * 65)
    print("开始执行 Grok 账号注册流程 (基于 CloakBrowser 指纹浏览器)...")
    print(f"计划注册数量: {total_count} 个")
    print(f"国家轮换队列: {', '.join(rotation_countries)}")
    print(f"显式代理设置: {args.proxy or '自动按国家分配 Resin 纯净粘性出口'}")
    print(f"初生探针自检: {'关闭' if args.no_probe else '开启'}")
    print(f"下游自动同步: {'开启 (gptGrok2api)' if args.sync else '关闭'}")
    print(f"运行模式配置: {'无头 (Headless)' if args.headless else '有界面 (GUI)' if args.headless is not None else '遵循环境配置'}")
    print("=" * 65)

    results = []

    for idx in range(total_count):
        country_for_account = rotation_countries[idx % len(rotation_countries)]
        print(f"\n>>> [{idx + 1}/{total_count}] 开始注册第 {idx + 1} 个 Grok 账号 (目标国家: {country_for_account}) <<<")

        proxy_for_account = args.proxy
        if not proxy_for_account:
            proxy_for_account = pick_proxy(country=country_for_account, for_registration=True)

        print(f"分配代理出口: {proxy_for_account}")

        res = register_single_grok_account(
            proxy=proxy_for_account,
            country=country_for_account,
            max_email_retries=args.max_retries,
            run_quality_probe=not args.no_probe,
            headless=args.headless,
            log_callback=lambda msg: print(msg),
        )

        res["target_country"] = country_for_account
        res["proxy_used"] = proxy_for_account

        if res.get("success"):
            print(f"[SUCCESS] 第 {idx + 1} 个 Grok 账号注册成功！")
            print(f"  邮箱: {res.get('email')}")
            print(f"  国家: {country_for_account}")
            print(f"  状态: {res.get('status')}")
            print(f"  SSO Token 前缀: {res.get('sso_token')[:20]}...")

            if args.sync:
                try:
                    from core.gptgrok2api_sync import push_grok_accounts_to_gptgrok2api
                    sync_payload = [{
                        "id": res.get("account_id"),
                        "email": res.get("email"),
                        "password": res.get("password"),
                        "sso_token": res.get("sso_token"),
                        "refresh_token": res.get("refresh_token"),
                        "access_token": res.get("access_token"),
                        "status": res.get("status"),
                        "country": country_for_account,
                        "proxy_used": proxy_for_account,
                    }]
                    sync_res = push_grok_accounts_to_gptgrok2api(sync_payload)
                    res["sync_result"] = sync_res
                    print(f"  [SYNC] 下游 gptGrok2api 同步结果: {sync_res.get('ok')}, added={sync_res.get('data', {}).get('added', 0)}")
                except Exception as sync_err:
                    res["sync_error"] = str(sync_err)
                    print(f"  [SYNC ERROR] 下游同步异常: {sync_err}")
        else:
            print(f"[FAILED] 第 {idx + 1} 个账号注册失败: {res.get('error')}")

        results.append(res)
        if idx < total_count - 1:
            time.sleep(2)

    print("\n" + "=" * 65)
    print("全部注册任务汇总报告：")
    success_count = sum(1 for r in results if r.get("success"))
    print(f"总计: {total_count} 个 | 成功: {success_count} 个 | 失败: {total_count - success_count} 个")
    for i, r in enumerate(results, 1):
        status_text = "成功" if r.get("success") else f"失败 ({r.get('error')})"
        print(f"  {i}. [{r.get('target_country')}] {r.get('email') or 'N/A'} -> {status_text}")
    print("=" * 65)


if __name__ == "__main__":
    main()
