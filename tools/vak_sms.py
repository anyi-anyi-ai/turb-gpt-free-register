# -*- coding: utf-8 -*-
"""
tools/vak_sms.py — VAK-SMS 接码与比价终端调试工具
功能：
  - balance: 查询账户余额
  - services: 模糊搜索支持的平台与服务代码
  - prices: 实时拉取指定服务全国家报价与库存，输出最低价排行榜
  - buy: 手动购买指定或最低价国家号码
  - wait: 等待指定激活 ID 的短信验证码
  - release: 释放号码 (bad 退款 / end 完成)
  - test: 运行双层重试状态机（单国最多 5~10 次换号 -> 顺延次低价国家）完整闭环测试
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time

# 确保能引入项目根目录模块
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

if sys.platform == "win32":
    import io
    if hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "buffer"):
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from config.env_loader import load_env
load_env()

from core.sms.vak_provider import VakSmsProvider, SERVICE_ALIASES, COUNTRY_NAMES
from core.sms.session import VakSmsSession
from core.sms.base import SmsProviderError, SmsNoNumbersError, SmsNoBalanceError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("tools.vak_sms")


def cmd_balance(args, provider: VakSmsProvider):
    """查询账户余额"""
    try:
        bal = provider.get_balance()
        print("\n" + "=" * 45)
        print(f"💰 VAK-SMS 当前账户余额: ${bal:.4f}")
        print("=" * 45 + "\n")
    except Exception as exc:
        print(f"\n❌ 查询余额失败: {exc}\n")


def cmd_services(args, provider: VakSmsProvider):
    """搜索平台与服务代码"""
    keyword = str(args.search or "").strip().lower()
    print("\n" + "=" * 60)
    print("📋 VAK-SMS 平台服务代码映射速查")
    print("=" * 60)
    print(f"{'平台标识 / 别名':<25} | {'官方服务码':<12} | {'说明'}")
    print("-" * 60)

    count = 0
    for alias, code in sorted(SERVICE_ALIASES.items()):
        if not keyword or keyword in alias or keyword in code:
            print(f"{alias:<25} | {code:<12} | 标准服务")
            count += 1

    print("-" * 60)
    print(f"共匹配到 {count} 个平台别名映射。\n")


def cmd_prices(args, provider: VakSmsProvider):
    """查询最低价排行榜"""
    service = args.service
    service_code = provider.resolve_service(service)
    max_price = float(args.max_price) if args.max_price else None
    top = int(args.top or 15)

    print(f"\n🔍 正在检索服务 [{service}] (官方码: {service_code}) 的全国家实时报价与余量...")
    try:
        tiers = provider.get_country_prices(service_code, max_price=max_price)
        if not tiers:
            print(f"⚠️ 未找到可用国家或全部超出价格上限 (max_price={max_price})。\n")
            return

        print("\n" + "=" * 70)
        print(f"🏆 [{service.upper()}] 全国家最低价格排行榜 (前 {min(top, len(tiers))} 名)")
        print("=" * 70)
        print(f"{'排名':<6} | {'国家代码':<8} | {'国家全名':<20} | {'单价 (USD)':<12} | {'当前库存 (pc)'}")
        print("-" * 70)

        for i, t in enumerate(tiers[:top], start=1):
            rank_tag = "🥇 最低" if i == 1 else f"#{i}"
            print(f"{rank_tag:<6} | {t.country:<8} | {t.country_name:<20} | ${t.price:<11.4f} | {t.count} pc")

        print("-" * 70)
        print(f"💡 调度引擎将自动锁定排名第一的 [{tiers[0].country_name}] 为初始取号国家！\n")
    except Exception as exc:
        print(f"❌ 获取报价失败: {exc}\n")


def cmd_buy(args, provider: VakSmsProvider):
    """手动购买号码"""
    service = args.service
    country = args.country
    max_price = float(args.max_price) if args.max_price else None

    print(f"\n🛒 正在下单取号: 服务={service}, 国家={country or '自动最低价'}...")
    try:
        num = provider.acquire_number(service=service, country=country, max_price=max_price)
        print("\n" + "=" * 50)
        print("🎉 取号成功！")
        print(f"📱 手机号码: {num.phone}")
        print(f"🌍 所属国家: {num.country}")
        print(f"🔑 激活 ID: {num.id}")
        print(f"💵 购买单价: ${num.price:.4f}")
        print("=" * 50)
        print(f"\n👉 后续收码命令: python tools/vak_sms.py wait --id {num.id}")
        print(f"👉 释放退款命令: python tools/vak_sms.py release --id {num.id} --status bad\n")
    except Exception as exc:
        print(f"❌ 取号失败: {exc}\n")


def cmd_wait(args, provider: VakSmsProvider):
    """等待短信验证码"""
    activation_id = args.id
    timeout = int(args.timeout or 90)

    print(f"\n⏳ 正在监听激活 ID: {activation_id} (超时: {timeout}s)...")
    try:
        code = provider.wait_for_sms_code(activation_id, timeout=timeout)
        if code:
            print("\n" + "=" * 45)
            print(f"📩 成功收到短信验证码: 【 {code} 】")
            print("=" * 45)
            # 自动完成
            provider.set_status(activation_id, "end")
            print("✅ 已自动标记完成 (status=end)。\n")
        else:
            print("\n⚠️ 等待超时，未收到短信。")
            # 自动退款
            provider.set_status(activation_id, "bad")
            print("🔄 已自动作废号码并释放退款 (status=bad)。\n")
    except Exception as exc:
        print(f"❌ 收码过程出现错误: {exc}\n")


def cmd_release(args, provider: VakSmsProvider):
    """释放号码"""
    activation_id = args.id
    status = args.status or "bad"
    print(f"\n⚙️ 正在设置激活 ID {activation_id} 状态为 [{status}]...")
    try:
        ok = provider.set_status(activation_id, status=status)
        if ok:
            action = "释放退款" if status == "bad" else "确认完成"
            print(f"✅ 操作成功！已执行【{action}】。\n")
        else:
            print(f"⚠️ 平台返回未能确认，可能已被释放。\n")
    except Exception as exc:
        print(f"❌ 释放失败: {exc}\n")


def cmd_test(args, provider: VakSmsProvider):
    """运行双层重试算法闭环测试"""
    service = args.service
    max_retries = int(args.max_retries or 3)
    max_hops = int(args.max_hops or 2)
    timeout = int(args.timeout or 15)  # 测试模式默认快速超时验证

    print("\n" + "=" * 65)
    print("🧪 正在启动双层重试调度算法模拟闭环测试")
    print(f"🎯 目标服务: {service}")
    print(f"🔁 单国换号上限: {max_retries} 次")
    print(f"🔀 最大允许跨国: {max_hops} 国")
    print("=" * 65 + "\n")

    try:
        with VakSmsSession(
            provider=provider,
            service=service,
            max_retries_per_country=max_retries,
            max_country_hops=max_hops,
            default_timeout=timeout,
        ) as session:
            for attempt in session.iter_numbers():
                print(f"\n[测试收到号码] +{attempt.raw_phone} | 国家={attempt.country_name} | 单价=${attempt.price:.4f}")
                print(f"-> 模拟网页填入该手机号并等待...")
                
                # 在测试模式下，可选择模拟超时换号
                code = attempt.wait_for_code(timeout=timeout)
                if code:
                    print(f"-> 测试成功收到验证码: {code}")
                    break
                else:
                    print(f"-> 未收到码，触发自动退款，准备在当前国家换号或顺延...")

    except Exception as exc:
        print(f"\n测试运行结束或捕获终止: {exc}\n")


def main():
    parser = argparse.ArgumentParser(
        description="VAK-SMS 官方买家 API 终端管理与智能比价工具",
    )
    parser.add_argument("--key", help="指定 VAK-SMS API Key (覆盖 .env)")
    parser.add_argument("--base-url", help="指定 VAK-SMS Base URL")

    subparsers = parser.add_subparsers(dest="command", required=True)

    # balance
    p_bal = subparsers.add_parser("balance", help="查询账户余额")

    # services
    p_serv = subparsers.add_parser("services", help="搜索平台别名与服务代码")
    p_serv.add_argument("--search", "-s", help="关键词搜索")

    # prices
    p_pri = subparsers.add_parser("prices", help="查看最低价国家排行榜")
    p_pri.add_argument("--service", required=True, help="服务名称或代码 (如 openai / claude / dr)")
    p_pri.add_argument("--max-price", help="最高价格上限 (USD)")
    p_pri.add_argument("--top", default=15, type=int, help="显示排行榜前 N 名")

    # buy
    p_buy = subparsers.add_parser("buy", help="手动购买号码")
    p_buy.add_argument("--service", required=True, help="服务名称或代码")
    p_buy.add_argument("--country", help="指定国家代码 (如 id, ru, us)；留空自动选最低价")
    p_buy.add_argument("--max-price", help="最高价格上限")

    # wait
    p_wait = subparsers.add_parser("wait", help="等待短信验证码")
    p_wait.add_argument("--id", required=True, help="激活 ID (idNum)")
    p_wait.add_argument("--timeout", default=90, type=int, help="超时秒数")

    # release
    p_rel = subparsers.add_parser("release", help="释放号码")
    p_rel.add_argument("--id", required=True, help="激活 ID")
    p_rel.add_argument("--status", choices=["bad", "end"], default="bad", help="状态: bad(退款) / end(完成)")

    # test
    p_test = subparsers.add_parser("test", help="双层换号与跨国顺延闭环算法测试")
    p_test.add_argument("--service", default="openai", help="服务名称")
    p_test.add_argument("--max-retries", default=3, type=int, help="单国换号上限")
    p_test.add_argument("--max-hops", default=2, type=int, help="跨国顺延上限")
    p_test.add_argument("--timeout", default=15, type=int, help="测试收码超时秒数")

    args = parser.parse_args()

    provider = VakSmsProvider(
        api_key=args.key,
        base_url=args.base_url,
    )

    cmd_map = {
        "balance": cmd_balance,
        "services": cmd_services,
        "prices": cmd_prices,
        "buy": cmd_buy,
        "wait": cmd_wait,
        "release": cmd_release,
        "test": cmd_test,
    }

    fn = cmd_map.get(args.command)
    if fn:
        fn(args, provider)


if __name__ == "__main__":
    main()
