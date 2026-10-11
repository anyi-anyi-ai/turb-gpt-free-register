# -*- coding: utf-8 -*-
"""精确定位并重置 KarinaWalters8478ZK@outlook.com 密码，提取 100 节点代理。"""
import os
import sys
import time
import re
import logging

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("recover_karina")

from core.cloakbrowser_driver import build_cloak_driver
from core.proxy_harvester.resin_egress import ResinEgressManager
from core.proxy_harvester.email_dispatcher import CascadingEmailDispatcher
from core.proxy_harvester.proxyscrape_provider import ProxyScrapeHarvester
from core.proxy_harvester.models import ProxyAccountRecord, save_account_record

EMAIL = "KarinaWalters8478ZK@outlook.com"
NEW_PASSWORD = "TurbProxy2026!#Scrape"

def main():
    logger.info(f"🌟 [ProxyScrape 资产变现提取] 目标账号: {EMAIL}")

    upstream = f"http://FAST_UPSTREAM.sess_karina:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260"
    logger.info(f"🌐 住宅出口: {upstream}")

    driver, _ = build_cloak_driver(
        headless=False,
        proxy=upstream,
        account_email=EMAIL,
    )
    page = driver.page

    harvester = ProxyScrapeHarvester(headless=False, upstream_proxy=upstream)
    dispatcher = CascadingEmailDispatcher()

    try:
        # 第一步：直接访问密码重置发起页
        reset_req_url = "https://dashboard.proxyscrape.com/v2/request-reset"
        logger.info(f"🌐 [步骤 1/4] 访问找回密码发起页: {reset_req_url}")
        
        nav_ok = False
        for i in range(3):
            try:
                page.goto(reset_req_url, timeout=35000, wait_until="domcontentloaded")
                nav_ok = True
                break
            except Exception as e:
                logger.warning(f"页面访问波动 ({e})，重试中...")
                page.wait_for_timeout(2500)
        
        if not nav_ok:
            logger.error("❌ 无法访问找回密码页面")
            return

        page.wait_for_timeout(2000)
        harvester._dismiss_cookie_banner(page)

        # 填入邮箱
        harvester._safe_fill_input(page, 'input[name="email"], input[type="email"]', EMAIL, "Email")
        logger.info(f"📧 已填入待重置邮箱: {EMAIL}")
        page.wait_for_timeout(1000)

        # 求解 Turnstile 激活提交按钮
        logger.info("⏳ 正在等待 Cloudflare Turnstile 求解 (单次点击后静默等待)...")
        harvester._solve_turnstile_widget(page)

        submit_btn = page.locator('button:has-text("Get password reset link"), button[type="submit"]').first
        
        # 轮询等待按钮启用 (25s 静默期 + 探针，杜绝重复连击打断加密握手)
        button_ready = False
        for cycle in range(40):
            page.wait_for_timeout(1000)
            is_disabled = page.evaluate("() => { const b = document.querySelector('button[type=\"submit\"]') || Array.from(document.querySelectorAll('button')).find(x => x.innerText.includes('reset link')); return !b || b.disabled; }")
            if not is_disabled:
                logger.info("🎉 Cloudflare 验证通过，[Get password reset link] 按钮已启用！")
                button_ready = True
                break
            # 仅在 25 秒静默期满后尝试一次补点
            if cycle == 25:
                logger.info("⏳ 25s 静默等待已满，尝试单次状态机自愈点击...")
                harvester._solve_turnstile_widget(page)

        # 点击发送重置链接
        try:
            submit_btn.click(force=True, timeout=3000)
        except Exception:
            page.evaluate("() => { const b = document.querySelector('button[type=\"submit\"]') || Array.from(document.querySelectorAll('button')).find(x => x.innerText.includes('reset link')); if (b) b.click(); }")
        
        logger.info("🚀 已点击提交重置申请，等待 Outlook Graph API 毫秒收信...")
        page.wait_for_timeout(4000)

        # 第二步：通过 Graph API 监听重置邮件
        logger.info(f"📩 [步骤 2/4] 监听 {EMAIL} 的重置邮件 (超时 90s)...")
        from core.outlook_client import get_account_context, _fetch_via, _http_session
        account_ctx = get_account_context(EMAIL)
        sess = _http_session()
        reset_link = None
        start_wait = time.time()
        while time.time() - start_wait < 90:
            try:
                msgs = _fetch_via(sess, "graph", account_ctx)
                for item in msgs:
                    subj = str(item.get("subject") or "").lower()
                    body = str(item.get("bodyPreview") or "")
                    if "reset" in subj or "password" in subj or "proxyscrape" in subj:
                        found_links = re.findall(r'https?://(?:dashboard\.)?proxyscrape\.com/v2/reset-password\?[^\s"\'<>]+', body)
                        if found_links:
                            reset_link = found_links[0].replace("&amp;", "&")
                            break
                        any_links = re.findall(r'https?://[^\s"\'<>]+', body)
                        for al in any_links:
                            if "reset-password" in al:
                                reset_link = al.replace("&amp;", "&")
                                break
                if reset_link:
                    break
            except Exception as e:
                logger.debug(f"Graph API 轮询波动: {e}")
            time.sleep(3)

        if not reset_link:
            logger.error("❌ 未能在超时时间内捕获到重置链接")
            return

        logger.info(f"🔗 成功捕获密码重置链接: {reset_link}")

        # 第三步：访问重置链接并设置固定强密码
        logger.info("🔑 [步骤 3/4] 访问重置链接，设置新密码...")
        page.goto(reset_link, timeout=35000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)
        harvester._dismiss_cookie_banner(page)

        # 填入 Password 与 Confirm New Password
        pwd_inps = page.locator('input[type="password"]').all()
        logger.info(f"找到 {len(pwd_inps)} 个密码输入框")
        for idx, pinp in enumerate(pwd_inps):
            try:
                pinp.fill(NEW_PASSWORD)
            except Exception:
                page.evaluate(f"""() => {{
                    const inps = document.querySelectorAll('input[type="password"]');
                    if (inps[{idx}]) inps[{idx}].value = '{NEW_PASSWORD}';
                }}""")

        page.wait_for_timeout(1000)
        # 求解可能的 Turnstile
        harvester._solve_turnstile_widget(page)

        # 点击 Reset password 提交
        reset_sub = page.locator('button:has-text("Reset password"), button[type="submit"]').first
        if reset_sub.count() > 0:
            reset_sub.click(force=True)
            logger.info("🚀 已点击 [Reset password] 确认重置")
            page.wait_for_timeout(4000)

        # 第四步：登录并提取 100 节点
        logger.info("🌐 [步骤 4/4] 访问控制台 / 登录页提取代理资产...")
        page.goto("https://dashboard.proxyscrape.com/v2/login", timeout=35000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        harvester._safe_fill_input(page, 'input[type="email"]', EMAIL, "Email")
        page.locator('button:has-text("Continue with email")').first.click(force=True)
        page.wait_for_timeout(2500)

        harvester._safe_fill_input(page, 'input[type="password"]', NEW_PASSWORD, "Password")
        page.locator('button:has-text("Sign In"), button[type="submit"]').first.click(force=True)
        page.wait_for_timeout(5000)

        # 进入控制台
        logger.info("🎉 正在提取已生成的 100 节点代理...")
        page.goto("https://dashboard.proxyscrape.com/v2/overview", timeout=35000, wait_until="domcontentloaded")
        page.wait_for_timeout(5000)
        harvester._dismiss_onboarding_modals(page)
        harvester._claim_free_trial_if_needed(page)

        proxies, sub_user, sub_pass = harvester._fetch_proxies_from_internal_api(page)

        if proxies:
            logger.info(f"🏆 [提取大成功] 成功抓取 ProxyScrape {len(proxies)} 个 7天粘性节点！")
            out_file = f"F:/github/turb-gpt-free-register/proxies_recovered_{EMAIL}_{len(proxies)}.txt"
            with open(out_file, "w", encoding="utf-8") as f:
                f.write("\n".join(proxies) + "\n")
            logger.info(f"📁 节点已成功落盘文件: {out_file}")

            # 落地 SQLite
            rec = ProxyAccountRecord(
                email=EMAIL,
                account_password=NEW_PASSWORD,
                email_source="outlook",
                sub_user=sub_user or "08746eee-02ae-4bb1-8f56-e794ada77c33",
                sub_pass=sub_pass or "",
                node_count=len(proxies),
                proxies=proxies,
                batch_id="recovered_20261009",
            )
            save_account_record(rec)
            logger.info("💾 资产已持久化到 turb.sqlite3 (harvested_proxy_accounts) 表！")
            
            # 自动联动 Resin 住宅网关
            from core.proxy_harvester.controller import HarvesterController
            ctrl = HarvesterController(target_accounts=1, provider="proxyscrape")
            ctrl._trigger_resin_sync(out_file, "recovered_20261009")
            logger.info("🚀 已自动联动触发 Resin 代理池热更注入！")
        else:
            logger.warning(f"⚠️ 控制台节点提取中，当前 URL: {page.url}")

    except Exception as ex:
        logger.error(f"❌ 运行异常: {ex}", exc_info=True)
    finally:
        try:
            driver.quit()
        except Exception:
            pass

if __name__ == "__main__":
    main()
