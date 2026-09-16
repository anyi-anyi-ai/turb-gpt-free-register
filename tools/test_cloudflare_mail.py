# -*- coding: utf-8 -*-
"""Cloudflare Worker 临时邮箱诊断与收信测试工具。

用法：
    # 1. 基础连通性测试：检查 API 连接与鉴权，创建测试邮箱并测试收件箱拉取
    python tools/test_cloudflare_mail.py

    # 2. 交互式收信测试：创建临时邮箱并持续监听 120 秒，等待外部发信并提取验证码
    python tools/test_cloudflare_mail.py --wait
"""
import argparse
import sys
import time
from pathlib import Path

# 适配 Windows 控制台编码
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 确保项目根目录在 sys.path 中
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from config import email as _email_cfg
from config.env_loader import load_env
from core import cf_temp_mail_client

load_env()


def main():
    parser = argparse.ArgumentParser(description="Cloudflare Worker 临时邮箱测试工具")
    parser.add_argument("--wait", action="store_true", help="创建邮箱后持续轮询等待接收邮件（120秒超时）")
    parser.add_argument("--timeout", type=int, default=120, help="等待邮件最长秒数，默认 120")
    args = parser.parse_args()

    api_base = getattr(_email_cfg, "CLOUDFLARE_API_BASE", "")
    auth_mode = getattr(_email_cfg, "CLOUDFLARE_AUTH_MODE", "none")
    api_key = getattr(_email_cfg, "CLOUDFLARE_API_KEY", "")
    domains = cf_temp_mail_client._default_domains()

    print("=" * 60)
    print("Cloudflare Worker 临时邮箱配置诊断")
    print("=" * 60)
    print(f"API 根地址      : {api_base or '(未配置)'}")
    print(f"鉴权模式        : {auth_mode}")
    print(f"API Key/密码    : {'已配置 (' + api_key[:4] + '***)' if api_key else '(空)'}")
    print(f"默认收信域名    : {domains or '(未指定，由 Worker 决定)'}")
    print("-" * 60)

    if not api_base:
        print("[错误] 未配置 CLOUDFLARE_API_BASE，请先在 .env 中填写。")
        return 1

    # 1. 尝试创建测试邮箱
    print("[1/3] 正在调用接口创建测试邮箱...")
    try:
        account = cf_temp_mail_client.pick_account()
        print(f"[成功] 成功生成测试邮箱: {account.email}")
        print(f"       所属域名: {account.domain or '(默认)'}")
        print(f"       JWT 凭据: {account.jwt[:15]}... (长度 {len(account.jwt)})")
    except Exception as exc:
        print(f"[失败] 创建邮箱失败: {exc}")
        return 1

    # 2. 尝试读取收件箱
    print("\n[2/3] 正在测试收件箱拉取接口 /api/mails...")
    try:
        messages = cf_temp_mail_client.list_messages(account.jwt)
        print(f"[成功] 邮件列表接口调用成功，当前收件箱邮件数: {len(messages)}")
    except Exception as exc:
        print(f"[失败] 拉取收件箱失败: {exc}")
        return 1

    # 3. 如果开启了 --wait，进行轮询等待
    if args.wait:
        print(f"\n[3/3] 进入实时收信等待模式（最长等待 {args.timeout} 秒）...")
        print(f"👉 请用你的手机/个人邮箱向此地址发送一封测试邮件：")
        print(f"   收件人: {account.email}")
        print(f"   主  题: 测试验证码 123456")
        print("-" * 60)

        deadline = time.time() + args.timeout
        received = False
        while time.time() < deadline:
            remaining = int(deadline - time.time())
            sys.stdout.write(f"\r正在轮询收件箱... 剩余 {remaining:3d} 秒 ")
            sys.stdout.flush()
            try:
                msgs = cf_temp_mail_client.list_messages(account.jwt)
                if msgs:
                    print("\n\n🎉 收到新邮件！")
                    for i, m in enumerate(msgs, 1):
                        probe = cf_temp_mail_client._otp_item(m)
                        sender = probe.get("from") or m.get("source") or "未知发件人"
                        subject = probe.get("subject") or "无主题"
                        print(f"  [{i}] 发件人: {sender}")
                        print(f"      主  题: {subject}")
                    received = True
                    break
            except Exception as exc:
                print(f"\n[警告] 轮询出错: {exc}")
            time.sleep(3)

        if not received:
            print(f"\n\n[提示] 在 {args.timeout} 秒内未收到邮件。")
            print("若外部已发送但未收到，请检查：")
            print("1. 域名 DNS 的 MX 记录是否已配置为 Cloudflare Email Routing；")
            print("2. Cloudflare Email Routing 是否配置了 Catch-all 规则指向该 Worker。")
    else:
        print("\n[3/3] 接口连通性测试完毕！")
        print("💡 提示：如需测试实际邮件收发，请运行：")
        print("   python tools/test_cloudflare_mail.py --wait")

    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
