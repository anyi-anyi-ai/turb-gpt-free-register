# -*- coding: utf-8 -*-
"""MailNest / 迈巢邮箱诊断与测试工具。

用法：
    # 1. 基础测试：检查 API Key 连通性、账户余额及项目库存
    python tools/test_mailnest.py

    # 2. 指定临时/独占邮箱测试获取一个邮箱并立即释放解冻
    python tools/test_mailnest.py --test-buy

    # 3. 指定某个邮箱查询是否有邮件/提取验证码
    python tools/test_mailnest.py --receive test@outlook.com
"""
import argparse
import sys
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
from core import mailnest_client

load_env()


def main():
    parser = argparse.ArgumentParser(description="MailNest 邮箱配置与接口测试工具")
    parser.add_argument("--api-key", help="指定 MailNest API Key（默认从 .env 读取）")
    parser.add_argument("--mode", choices=["temporary", "exclusive"], help="指定模式（temporary/exclusive）")
    parser.add_argument("--project-code", help="指定临时邮箱项目代码（默认从配置读取）")
    parser.add_argument("--test-buy", action="store_true", help="测试购买一个邮箱并主动释放（验证完整收放流程）")
    parser.add_argument("--receive", help="查询指定邮箱地址的邮件与验证码")
    args = parser.parse_args()

    if args.api_key:
        _email_cfg.MAIL_NEST_API_KEY = args.api_key
    if args.mode:
        _email_cfg.MAIL_NEST_MODE = args.mode
    if args.project_code:
        _email_cfg.MAIL_NEST_PROJECT_CODE = args.project_code

    api_base = getattr(_email_cfg, "MAIL_NEST_API_BASE", "https://mailnest.top")
    api_key = getattr(_email_cfg, "MAIL_NEST_API_KEY", "")
    mode = getattr(_email_cfg, "MAIL_NEST_MODE", "temporary")
    project_code = getattr(_email_cfg, "MAIL_NEST_PROJECT_CODE", "chatgpt001")

    print("=" * 60)
    print("MailNest / 迈巢 邮箱接入诊断")
    print("=" * 60)
    print(f"API Base     : {api_base}")
    print(f"API Key      : {'*' * 8 + api_key[-4:] if len(api_key) > 4 else '(未配置)'}")
    print(f"购买模式     : {mode}")
    print(f"项目代码     : {project_code}")
    print("-" * 60)

    # 1. 检查公开产品信息（无需 API Key）
    print("\n[1/3] 获取产品信息与库存 (GET /api/product/info)...")
    try:
        product_info = mailnest_client.get_product_info()
        temporary = product_info.get("temporary") or []
        exclusive = product_info.get("exclusive") or {}

        # 查找 ChatGPT 项目
        chatgpt_item = next((item for item in temporary if item.get("code") == project_code), None)
        if chatgpt_item:
            print(f"  [+] 临时邮箱项目 [{chatgpt_item.get('name')}] (code={project_code}): "
                  f"库存={chatgpt_item.get('stock')}, 价格={chatgpt_item.get('price')}元, "
                  f"有效期={chatgpt_item.get('duration_seconds')}秒")
        else:
            print(f"  [!] 未在临时项目列表中找到 code={project_code}，共有 {len(temporary)} 个可用项目")

        print(f"  [+] 独占邮箱: 库存={exclusive.get('stock')}, 价格={exclusive.get('price')}元")
    except Exception as exc:
        print(f"  [-] 获取产品信息失败: {exc}")
        return 1

    # 2. 检查 API Key 鉴权与账户余额
    print("\n[2/3] 验证 API Key 并查询账户余额 (GET /api/v1/balance)...")
    if not api_key:
        print("  [-] 缺少 MAIL_NEST_API_KEY，无法查询余额或调用购买接口。")
        print("      请在 .env 或 WebUI「配置 → 邮箱 / OTP」填写 MAIL_NEST_API_KEY。")
        return 1

    try:
        balance = mailnest_client.get_balance()
        print(f"  [+] 总余额    : {balance.get('balance')} 元")
        print(f"  [+] 冻结余额  : {balance.get('frozen_balance')} 元")
        print(f"  [+] 可用余额  : {balance.get('available_balance')} 元")
    except Exception as exc:
        print(f"  [-] 查询余额失败: {exc}")
        return 1

    # 3. 指定邮箱接收测试
    if args.receive:
        target_email = args.receive.strip()
        print(f"\n[3/3] 正在查询指定邮箱的邮件: {target_email}...")
        try:
            mails = mailnest_client._get_mails(target_email)
            print(f"  [+] 收件箱返回 {len(mails) if isinstance(mails, list) else 0} 封邮件")
            if isinstance(mails, list) and mails:
                for idx, mail in enumerate(mails, 1):
                    item = mailnest_client._otp_item(mail)
                    print(f"      - 邮件 #{idx}: 主题={item.get('subject')}, 发件人={item.get('from')}, "
                          f"验证码匹配={mail.get('code_match')}")
        except Exception as exc:
            print(f"  [-] 查询邮件失败: {exc}")
        return 0

    # 4. 可选：测试购买并释放流程
    if args.test_buy:
        print(f"\n[3/3] 执行购买与释放解冻测试 (mode={mode})...")
        try:
            account = mailnest_client.pick_account()
            print(f"  [+] 成功获取邮箱: {account.email} (id={account.mailbox_id})")
            print("  [+] 正在调用释放接口以解冻余额...")
            mailnest_client.release_account(account.email, status="cancelled", note="diagnostic_test")
            print("  [+] 释放完成，余额已解冻。")
        except Exception as exc:
            print(f"  [-] 测试购买/释放失败: {exc}")
            return 1
    else:
        print("\n[3/3] 提示: 添加 --test-buy 参数可进行实际购买与主动解冻释放测试。")

    print("\n" + "=" * 60)
    print("MailNest 配置测试通过！")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
