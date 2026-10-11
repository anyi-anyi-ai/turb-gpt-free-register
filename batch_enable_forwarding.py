# -*- coding: utf-8 -*-
"""
UNED 学生邮箱自动转发批量配置系统 (Office 365 / Playwright 驱动)
功能：
1. 自动登录微软 Office 365 (通过 UNED SSO 联合认证)
2. 自动打开 Outlook Web App 设置面板
3. 开启自动转发 (Forwarding)，将学校邮箱自动转寄到绑定的个人 Outlook 邮箱
4. 勾选“在收件箱中保留副本”
5. 保存配置并在 SQLite 与 Obsidian 中标记状态
"""

import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

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
logger = logging.getLogger("uned.forwarding")

def enable_forwarding_for_account(page, uid: str, pwd: str, personal_email: str) -> dict:
    """使用 Playwright 为单个账号开启自动转发"""
    school_email = f"{uid}@alumno.uned.es"
    try:
        # [1] 微软登录页
        page.goto("https://login.microsoftonline.com", wait_until="networkidle", timeout=25000)
        page.fill("#i0116", school_email)
        page.click("#idSIButton9")
        page.wait_for_timeout(3500)

        # [2] UNED 联合认证 SSO 页
        if page.locator("#ContentPlaceHolder1_ssousername").count() == 0:
            # 尝试等待跳转
            page.wait_for_selector("#ContentPlaceHolder1_ssousername", timeout=10000)

        page.fill("#ContentPlaceHolder1_ssousername", uid)
        page.fill("#ContentPlaceHolder1_password", pwd)
        if page.locator("#password").count() > 0:
            page.fill("#password", pwd)

        if page.locator("#ContentPlaceHolder1_Button1").is_visible():
            page.click("#ContentPlaceHolder1_Button1")
        elif page.locator("#ContentPlaceHolder1_enviar").is_visible():
            page.click("#ContentPlaceHolder1_enviar")
        else:
            page.evaluate("() => WebForm_DoPostBackWithOptions(new WebForm_PostBackOptions('ctl00$ContentPlaceHolder1$Button1', '', true, '', '', false, false))")

        # [3] 保持登录态 (Stay signed in)
        try:
            page.wait_for_selector("#idSIButton9, input[value='Yes'], button:has-text('Yes')", timeout=15000)
            page.click("#idSIButton9, input[value='Yes'], button:has-text('Yes')")
            page.wait_for_timeout(3000)
        except Exception:
            pass

        # [4] 访问 Outlook 邮箱
        for attempt in range(1, 4):
            try:
                page.goto("https://outlook.office.com/mail/", wait_until="domcontentloaded", timeout=40000)
                break
            except Exception:
                page.wait_for_timeout(3000)

        # 处理可能出现的 Pick an account / Stay signed in
        for _ in range(6):
            if page.locator("button#settingsId, button[title='Settings'], button[aria-label='Settings']").count() > 0:
                break
            if page.locator("text='Pick an account'").count() > 0 or page.locator("div:has-text('Signed in')").count() > 0:
                page.locator("div:has-text('Signed in'), [role='listitem'], #tilesHolder > div").first.click()
                page.wait_for_timeout(4000)
            if page.locator("#idSIButton9, input[value='Yes'], button:has-text('Yes')").count() > 0:
                page.locator("#idSIButton9, input[value='Yes'], button:has-text('Yes')").first.click()
                page.wait_for_timeout(4000)
            page.wait_for_timeout(1000)

        # [5] 打开设置面板
        page.wait_for_selector("button#settingsId, button[title='Settings'], button[aria-label='Settings']", timeout=30000)
        page.click("button#settingsId, button[title='Settings'], button[aria-label='Settings']")
        page.wait_for_timeout(2500)

        # [5] 调出 Forwarding 菜单
        page.fill("input[placeholder*='Search settings']", "Forwarding")
        page.wait_for_timeout(1000)
        page.click("div[role='listbox'] div:has-text('Forwarding'), [role='option']:has-text('Forwarding')")
        page.wait_for_timeout(2500)

        # [6] 检查当前开关状态
        lbl = page.locator("label:has-text('Enable forwarding'), span:text-is('Enable forwarding')")
        inp = page.locator("input[placeholder*='Enter an email address']")

        if inp.count() == 0 or not inp.first.is_visible():
            # 开关未开启，点击开启
            lbl.first.click()
            page.wait_for_timeout(1500)

        # [7] 填入个人 Outlook 邮箱
        inp = page.locator("input[placeholder*='Enter an email address']")
        if inp.count() > 0:
            inp.first.fill(personal_email)
            page.wait_for_timeout(800)

        # [8] 勾选保留副本
        keep_cb = page.locator("label:has-text('Keep a copy of forwarded messages')")
        if keep_cb.count() > 0:
            keep_cb.first.click()
            page.wait_for_timeout(500)

        # [9] 点击保存
        save_btn = page.locator("button:has-text('Save'):visible")
        if save_btn.count() > 0:
            save_btn.first.click()
            page.wait_for_timeout(3500)

        return {"success": True, "message": f"成功配置自动转发至 {personal_email}"}

    except Exception as e:
        return {"success": False, "message": str(e)}

def run_batch_forwarding(limit: Optional[int] = None, only_confirmed: bool = True):
    conn = sqlite3.connect("turb.sqlite3")
    cur = conn.cursor()
    cur.execute("SELECT id, uned_identifier, email, payload_json, forwarding_enabled FROM uned_accounts ORDER BY id ASC")
    rows = cur.fetchall()
    conn.close()

    total = len(rows)
    print("\n" + "=" * 65)
    print(f"       📬 UNED 学校邮箱自动转发批量配置系统 (总库: {total} 个账号)")
    print("=" * 65 + "\n")

    success_cnt = 0
    skip_cnt = 0
    fail_cnt = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"]
        )

        for idx, (acc_id, uid, personal_email, p_str, fwd_enabled) in enumerate(rows, 1):
            if limit and idx > limit:
                break

            if fwd_enabled:
                print(f"[{idx}/{total}] [跳过-已开启转发] {uid} -> {personal_email}")
                skip_cnt += 1
                success_cnt += 1
                continue

            pwd = "Uned@2026!Zhang#88"
            extra = {}
            phone = ""
            doc_num = ""
            full_name = ""
            created_at = ""
            try:
                p_obj = json.loads(p_str)
                extra = p_obj.get("extra", {})
                pwd = extra.get("password") or pwd
                phone = p_obj.get("phone", "")
                doc_num = p_obj.get("doc_number", "")
                full_name = p_obj.get("full_name", "")
                created_at = p_obj.get("created_at", "")
            except Exception:
                pass

            print(f"\n[{idx}/{total}] 正在配置账号: {uid} ({uid}@alumno.uned.es)")
            print(f"[{idx}/{total}] 目标转发地址: {personal_email}")

            context = browser.new_context(
                locale="en-US",
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
            )
            page = context.new_page()

            res = enable_forwarding_for_account(page, uid, pwd, personal_email)
            context.close()

            if res["success"]:
                print(f"[{idx}/{total}] [成功] {uid} | {res['message']}")
                success_cnt += 1
                try:
                    conn = sqlite3.connect("turb.sqlite3")
                    cur = conn.cursor()
                    cur.execute("UPDATE uned_accounts SET forwarding_enabled=1 WHERE uned_identifier=?", (uid,))
                    conn.commit()
                    conn.close()

                    # 同步到 Obsidian
                    acc_info = {
                        "uned_identifier": uid,
                        "student_email": f"{uid}@alumno.uned.es",
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
                except Exception as e_db:
                    logger.debug(f"DB update error: {e_db}")
            else:
                print(f"[{idx}/{total}] [失败/需激活] {uid} | {res['message'][:80]}")
                fail_cnt += 1

            time.sleep(1.0)

        browser.close()

    try:
        update_summary_table()
    except Exception:
        pass

    print("\n" + "=" * 65)
    print(f"批量配置任务结束！成功/已配置: {success_cnt} | 失败/未激活: {fail_cnt}")
    print("=" * 65 + "\n")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()
    run_batch_forwarding(limit=args.limit)
