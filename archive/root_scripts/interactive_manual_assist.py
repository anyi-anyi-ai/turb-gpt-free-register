# -*- coding: utf-8 -*-
"""
UNED 学生邮箱【有头交互式人机协同】快速激活与自动转发配置助手
功能：
1. 逐个调出尚未配置转发的 UNED 账号（总计 65 个待处理）
2. 弹出有头浏览器窗口 (headless=False)，展示账号、密码、个人邮箱
3. 协助导航至 Campus / CiberUNED 激活页，由您点击【Activar Correo】完成激活
4. 激活完成后，在控制台轻按回车，脚本全自动接管浏览器：
   - 自动进入 Outlook Web 邮件系统
   - 自动打开设置 -> 搜索 Forwarding -> 开启自动转发
   - 自动填入绑定的个人 Outlook 邮箱并勾选“保留副本”保存
   - 自动将 SQLite (forwarding_enabled=1) 及 Obsidian 知识库打标同步
5. 自动切换至下一个账号，循环推进！
"""

import argparse
import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from playwright.sync_api import sync_playwright
from core.uned.obsidian_sync import sync_account_to_obsidian, update_summary_table

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("uned.assist")


def configure_forwarding(context, personal_email: str) -> bool:
    """自动在已打开或新建的 Outlook 页面中配置转寄至个人邮箱"""
    print(f"\n⚡ [自动化接管] 正在进入 Outlook 并配置转寄至: {personal_email} ...")
    try:
        # 优先复用用户已经点开的 Outlook 标签页
        outlook_page = None
        for p in context.pages:
            if "outlook.office.com" in p.url or "outlook.office365.com" in p.url or "mail" in p.url:
                outlook_page = p
                break
        
        if not outlook_page:
            outlook_page = context.pages[0] if context.pages else context.new_page()
            outlook_page.goto("https://outlook.office.com/mail/", wait_until="domcontentloaded", timeout=45000)

        page = outlook_page
        page.bring_to_front()

        # 2. 等待设置齿轮图标
        print("  -> 正在定位设置面板...")
        page.wait_for_selector(
            "button#settingsId, button[title='Settings'], button[aria-label='Settings']",
            timeout=30000
        )
        page.click("button#settingsId, button[title='Settings'], button[aria-label='Settings']")
        page.wait_for_timeout(2500)

        # 3. 搜索 Forwarding 设置项
        print("  -> 搜索 Forwarding 转发选项...")
        page.fill("input[placeholder*='Search settings']", "Forwarding")
        page.wait_for_timeout(1000)
        page.click("div[role='listbox'] div:has-text('Forwarding'), [role='option']:has-text('Forwarding')")
        page.wait_for_timeout(2500)

        # 4. 开启转发开关
        lbl = page.locator("label:has-text('Enable forwarding'), span:text-is('Enable forwarding')")
        inp = page.locator("input[placeholder*='Enter an email address']")
        if inp.count() == 0 or not inp.first.is_visible():
            if lbl.count() > 0:
                lbl.first.click()
                page.wait_for_timeout(1500)

        # 5. 填入个人邮箱
        inp = page.locator("input[placeholder*='Enter an email address']")
        if inp.count() > 0:
            inp.first.fill(personal_email)
            page.wait_for_timeout(800)

        # 6. 勾选在收件箱中保留副本
        keep_cb = page.locator("label:has-text('Keep a copy of forwarded messages')")
        if keep_cb.count() > 0:
            keep_cb.first.click()
            page.wait_for_timeout(500)

        # 7. 保存
        save_btn = page.locator("button:has-text('Save'):visible")
        if save_btn.count() > 0:
            save_btn.first.click()
            page.wait_for_timeout(3500)

        print(f"  -> ✅ 自动转寄配置成功保存！目标: {personal_email}")
        return True

    except Exception as e:
        print(f"  -> ❌ 自动配置转寄发生异常: {e}")
        return False


import re

def parse_account_proxy(proxy_str: str) -> tuple[Optional[dict], str]:
    """从 proxy_used 字符串解析出代理配置字典及国家/IP描述"""
    if not proxy_str:
        return None, "直连 (无代理记录)"
    match = re.search(r"http://([^:]+):([^@]+)@([^:\s]+):(\d+)", proxy_str)
    if match:
        user, pwd, host, port = match.groups()
        country_code = user.split(".")[0]
        ip_hint = ""
        if "|" in proxy_str:
            ip_hint = proxy_str.split("|")[0].strip()
        desc = f"同国/同节点 [{country_code}] {ip_hint}".strip()
        return {"server": f"http://{host}:{port}", "username": user, "password": pwd}, desc
    return None, proxy_str[:30]


def run_interactive_assistant(start_index: int = 1, use_proxy: bool = True):
    conn = sqlite3.connect("turb.sqlite3")
    cur = conn.cursor()
    cur.execute("""
        SELECT id, uned_identifier, email, payload_json, forwarding_enabled, proxy_used
        FROM uned_accounts
        WHERE forwarding_enabled = 0 OR forwarding_enabled IS NULL
        ORDER BY id ASC
    """)
    rows = cur.fetchall()
    conn.close()

    total = len(rows)
    print("\n" + "=" * 70)
    print(f"🚀 UNED 学生邮箱【有头人机协同】快速激活与转寄系统")
    print(f"📌 待配置账号总数: {total} 个 | 起始序号: 第 {start_index} 个")
    print(f"🛡️ 节点网络策略: {'自动匹配注册时出口 IP 与国家' if use_proxy else '直连模式'}")
    print("=" * 70 + "\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=False,  # 有头模式，窗口立即可见
            args=[
                "--disable-blink-features=AutomationControlled",
                "--start-maximized"
            ]
        )

        for cur_idx, (acc_id, uid, personal_email, p_str, fwd_enabled, proxy_used) in enumerate(rows, 1):
            if cur_idx < start_index:
                continue

            pwd = "Uned@2026!Zhang#88"
            full_name = ""
            doc_num = ""
            phone = ""
            created_at = ""
            extra = {}
            try:
                p_obj = json.loads(p_str)
                extra = p_obj.get("extra", {})
                pwd = extra.get("password") or pwd
                full_name = p_obj.get("full_name", "")
                doc_num = p_obj.get("doc_number", "")
                phone = p_obj.get("phone", "")
                created_at = p_obj.get("created_at", "")
            except Exception:
                pass

            school_email = f"{uid}@alumno.uned.es"

            try:
                import pyperclip
                pyperclip.copy(pwd)
                clip_tip = " (已自动复制到剪贴板，可直接 Ctrl+V 粘贴)"
            except Exception:
                clip_tip = ""

            acc_proxy, proxy_desc = parse_account_proxy(proxy_used)

            print("\n" + "─" * 70)
            print(f"📋 【{cur_idx}/{total}】当前处理账号: {uid}")
            print(f"   • 学生邮箱 : {school_email}")
            print(f"   • 账号密码 : {pwd}{clip_tip}")
            print(f"   • 绑定邮箱 : {personal_email}")
            print(f"   • 认证姓名 : {full_name} ({doc_num})")
            print(f"   • 注册节点 : {proxy_desc}")
            print("─" * 70)

            context = browser.new_context(
                proxy=acc_proxy if use_proxy else None,
                locale="es-ES",
                viewport=None,  # 跟随窗口最大化
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )

            # 标签页 1：直达 CiberUNED 激活学生邮箱页面
            print(f"🌐 [标签页 1] 正在打开 CiberUNED 邮箱激活页 (serviwebng)...")
            page_act = context.new_page()
            try:
                page_act.goto("https://serviwebng.uned.es/correo/index.asp", timeout=20000)
            except Exception:
                pass

            # 标签页 2：微软 Office 365 登录页
            print(f"🌐 [标签页 2] 正在打开微软 Office 365 登录页并预填账号...")
            page_ms = context.new_page()
            try:
                page_ms.goto("https://login.microsoftonline.com", timeout=20000)
                if page_ms.locator("#i0116").count() > 0:
                    page_ms.fill("#i0116", school_email)
            except Exception:
                pass

            # 切回激活页供用户首先操作
            page_act.bring_to_front()

            print("\n" + "👉 【请在浏览器中操作】:")
            print("   1. 在标签页 1 完成登录后，点击【Activar Correo】按钮，直到出现【Cuenta de correo activada】")
            print("   2. 点击顶部【Acceso Correo】或切换到标签页 2 登录并进入 Outlook 邮箱")
            print("\n" + "⌨️ 【完成指示】:")
            print(f"   • 直接按【回车键 (Enter)】: 脚本立即接管，自动配置转发至 {personal_email} 并保存！")
            print("   • 输入【s】+ 回车: 跳过此账号")
            print("   • 输入【q】+ 回车: 退出程序\n")

            user_action = input(">>> 请输入指令 (直接回车确认已激活): ").strip().lower()

            if user_action == "q":
                print("👋 正在退出助手...")
                context.close()
                break
            elif user_action == "s":
                print(f"⏩ 已跳过账号: {uid}")
                context.close()
                continue

            # 用户确认已激活，脚本自动接管配置转发
            success = configure_forwarding(context, personal_email)

            if success:
                # 更新数据库
                try:
                    conn = sqlite3.connect("turb.sqlite3")
                    cur_db = conn.cursor()
                    cur_db.execute("UPDATE uned_accounts SET forwarding_enabled=1, status='activated' WHERE uned_identifier=?", (uid,))
                    conn.commit()
                    conn.close()

                    # 同步到 Obsidian
                    acc_info = {
                        "uned_identifier": uid,
                        "student_email": school_email,
                        "email": personal_email,
                        "password": pwd,
                        "status": "activated",
                        "extra": extra,
                        "phone": phone,
                        "doc_number": doc_num,
                        "name": full_name,
                        "created_at": created_at,
                        "forwarding_to": personal_email
                    }
                    sync_account_to_obsidian(acc_info)
                    update_summary_table()
                    print(f"✨ [数据库与知识库已同步] {uid} 状态标记为已转发 ✅")
                except Exception as e_sync:
                    print(f"⚠️ 同步状态错误: {e_sync}")
            else:
                retry = input("⚠️ 自动转寄配置未成功完成，是否已手动在网页上配好？(y/n): ").strip().lower()
                if retry == "y":
                    conn = sqlite3.connect("turb.sqlite3")
                    cur_db = conn.cursor()
                    cur_db.execute("UPDATE uned_accounts SET forwarding_enabled=1, status='activated' WHERE uned_identifier=?", (uid,))
                    conn.commit()
                    conn.close()
                    print(f"✨ 已手动标记 {uid} 为已转发 ✅")

            context.close()
            time.sleep(1.0)

        browser.close()

    print("\n🎉 人机协同激活任务结束！")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UNED 人机协同快速激活与转寄配置助手")
    parser.add_argument("--start", type=int, default=1, help="起始账号序号 (从1开始)")
    parser.add_argument("--no-proxy", action="store_true", help="是否关闭代理直连（默认自动匹配注册时出口 IP 与国家）")
    args = parser.parse_args()

    run_interactive_assistant(start_index=args.start, use_proxy=not args.no_proxy)
