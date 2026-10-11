# -*- coding: utf-8 -*-
"""一键恢复已注册 ProxyScrape 账号并全量提取 100 节点代理资产。

支持：
1. 具有已知密码的账号（如 LaurenEstrada）：直接免密/密码登录
2. 未知密码但已注册的账号（如 SavannahCollins, KarlBrown, RigelArrietty, tanxiulan, PaulaJiraiya）：
   自动访问 /v2/request-reset 发起重置，通过 Outlook Graph API 捕获重置邮件设置密码并登录
3. 进入控制台后，调用已补齐 protocol=http 的接口并全量提取 100 个 7 天粘性节点，更新落库 turb.sqlite3
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
import time
from pathlib import Path

# 控制台 UTF-8
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("recover_proxyscrape")

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.cloakbrowser_driver import build_cloak_driver
from core.proxy_harvester.resin_egress import ResinEgressManager
from core.proxy_harvester.email_dispatcher import CascadingEmailDispatcher
from core.proxy_harvester.proxyscrape_provider import ProxyScrapeHarvester
from core.proxy_harvester.models import ProxyAccountRecord, save_account_record

DEFAULT_RECOVER_PASSWORD = "TurbProxy2026!#Scrape"


def get_known_password(email: str, db_path: Path | str = "turb.sqlite3") -> str:
    """从数据库检索已知账号密码。"""
    try:
        with sqlite3.connect(db_path) as conn:
            r = conn.execute(
                "SELECT account_password FROM harvested_proxy_accounts WHERE lower(email) = lower(?) AND account_password != '' AND account_password != 'recovered_oauth'",
                (email,),
            ).fetchone()
            if r and r[0]:
                return r[0].strip()
    except Exception:
        pass
    return ""


def update_db_password(email: str, password: str, db_path: Path | str = "turb.sqlite3") -> None:
    try:
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE harvested_proxy_accounts SET account_password = ? WHERE lower(email) = lower(?)", (password, email))
            conn.commit()
    except Exception:
        pass


def reset_password_flow(page, harvester, dispatcher, email: str, new_password: str = DEFAULT_RECOVER_PASSWORD) -> bool:
    """在 ProxyScrape /v2/request-reset 发起重置密码并通过 Outlook 捕获链接完成重置。"""
    logger.info(f"🔄 启动找回密码流程: {email}")
    reset_url = "https://dashboard.proxyscrape.com/v2/request-reset"
    try:
        page.goto(reset_url, timeout=35000, wait_until="domcontentloaded")
        page.wait_for_timeout(2500)
        harvester._dismiss_cookie_banner(page)

        em_inp = page.locator('input[type="email"], input[name="email"]').first
        em_inp.wait_for(state="visible", timeout=10000)
        em_inp.click()
        em_inp.fill(email)

        page.evaluate("""(em) => {
            const el = document.querySelector('input[type="email"], input[name="email"]');
            if (el) {
                el.value = em;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.dispatchEvent(new Event('blur', { bubbles: true }));
            }
        }""", email)
        page.wait_for_timeout(500)

        logger.info("⏳ 等待 Turnstile 验证以提交重置密码请求...")
        for _ in range(45):
            passed = harvester._solve_turnstile_widget(page)
            if passed:
                logger.info("🎉 Turnstile 验证通过，点击发送重置邮件...")
                sub_btn = page.locator('button[type="submit"]').first
                if sub_btn.count() > 0:
                    sub_btn.click(force=True)
                break
            page.wait_for_timeout(1000)

        page.wait_for_timeout(3000)
        logger.info("📩 正在监听 Outlook Graph API 捕获来自 ProxyScrape 的密码重置邮件...")

        # 检查页面是否转变成输入验证码模式
        has_code_input = page.locator('input[placeholder*="code" i], input[name="code"], input[type="text"]:not([type="email"])').count() > 0

        # 直接使用底层 Outlook 接口抓取最近收到的 ProxyScrape 邮件
        from core.outlook_client import get_account_context, _fetch_via, _http_session
        sess = _http_session()
        ctx = get_account_context(email)

        found_link = None
        found_code = None
        start_wait = time.time()

        while time.time() - start_wait < 90:
            if ctx:
                try:
                    msgs = _fetch_via(sess, "graph", ctx)
                    for m in msgs:
                        subj = str(m.get("subject") or "")
                        sender = str(m.get("from") or "")
                        if "proxyscrape" in subj.lower() or "proxyscrape" in sender.lower() or "reset" in subj.lower():
                            body_prev = str(m.get("bodyPreview") or "")
                            body_html = str(m.get("bodyHtml") or "")
                            full_m = f"{subj}\n{body_prev}\n{body_html}"
                            logger.info(f"📨 探测到 ProxyScrape 邮件: Subject='{subj}', Preview='{body_prev[:120]}'")

                            # 尝试提取链接
                            links = re.findall(r'https?://[^\s"\'<>]+(?:reset[^\s"\'<>]*|token=[^\s"\'<>]*)', full_m, re.I)
                            if links:
                                found_link = links[0].replace("&amp;", "&")
                                break

                            # 尝试提取验证码
                            codes = re.findall(r'(?:code|verification code|pin)[:\s]+([a-zA-Z0-9]{6,12})', full_m, re.I)
                            if codes:
                                found_code = codes[0]
                                break
                            # 或者匹配 10 位 hex 码
                            hex_codes = re.findall(r'\b([a-f0-9]{10})\b', full_m)
                            if hex_codes:
                                found_code = hex_codes[0]
                                break
                except Exception as ex:
                    logger.debug(f"轮询邮件异常: {ex}")

            if found_link or found_code:
                break
            page.wait_for_timeout(3000)

        if found_link:
            logger.info(f"🔗 捕获到密码重置链接: {found_link}")
            page.goto(found_link, timeout=35000, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            pwds = page.locator('input[type="password"]').all()
            if len(pwds) >= 1:
                for p_el in pwds:
                    p_el.fill(new_password)
                    page.wait_for_timeout(200)

                sub_reset = page.locator('button[type="submit"], button:has-text("Reset"), button:has-text("Save")').first
                if sub_reset.count() > 0:
                    sub_reset.click(force=True)
                page.wait_for_timeout(3000)
                logger.info(f"✅ 密码已成功重置为: {new_password}")
                update_db_password(email, new_password)
                return True
            else:
                logger.error("❌ 重置密码页面未检测到密码输入框")
                return False

        elif found_code:
            logger.info(f"🔢 捕获到重置验证码: {found_code}")
            code_inp = page.locator('input[placeholder*="code" i], input[name="code"], input[type="text"]').first
            if code_inp.count() > 0:
                code_inp.fill(found_code)
                page.wait_for_timeout(300)
            pwd_inp = page.locator('input[type="password"]').first
            if pwd_inp.count() > 0:
                pwd_inp.fill(new_password)
                page.wait_for_timeout(300)
            sub_btn = page.locator('button[type="submit"], button:has-text("Submit"), button:has-text("Continue")').first
            if sub_btn.count() > 0:
                sub_btn.click(force=True)
            page.wait_for_timeout(3000)
            update_db_password(email, new_password)
            return True
        else:
            logger.error("❌ 未能在超时时间内捕获到密码重置邮件 (链接或验证码)")
            return False
    except Exception as ex:
        logger.error(f"❌ 找回密码流程异常: {ex}")
        return False


def recover_and_extract(email: str, password: str | None = None, headed: bool = True) -> list[str]:
    logger.info("=" * 80)
    logger.info(f"🚀 开始为已注册账号恢复会话并提取节点: {email}")
    logger.info("=" * 80)

    known_password = password or get_known_password(email)
    if known_password:
        logger.info(f"🔑 检索到本地已持久化密码: {known_password[:3]}***{known_password[-2:]}")
    else:
        logger.info("ℹ️ 本地无预存密码，将先通过 Outlook Graph API 自动重置密码并完成登录")

    egress = ResinEgressManager(platform="Default")
    upstream = egress.get_account_proxy(account_index=1, batch_tag="recovery", target_url="https://dashboard.proxyscrape.com/v2/login")
    logger.info(f"🌐 住宅出口: {upstream}")

    driver, _ = build_cloak_driver(
        headless=not headed,
        proxy=upstream,
        account_email=email,
    )
    page = driver.page

    harvester = ProxyScrapeHarvester(headless=not headed, upstream_proxy=upstream)
    dispatcher = CascadingEmailDispatcher()

    try:
        # 如果不知道密码，先执行一次密码重置
        if not known_password:
            ok = reset_password_flow(page, harvester, dispatcher, email, DEFAULT_RECOVER_PASSWORD)
            if ok:
                known_password = DEFAULT_RECOVER_PASSWORD
            else:
                logger.warning("⚠️ 密码重置未成功，尝试直接进行登录探测...")

        login_url = "https://dashboard.proxyscrape.com/v2/login"
        nav_ok = False
        for nav_idx in range(4):
            try:
                logger.info(f"🌐 访问登录页 (第 {nav_idx+1}/4 次尝试): {login_url}")
                page.goto(login_url, timeout=35000, wait_until="domcontentloaded")
                nav_ok = True
                break
            except Exception as n_err:
                logger.warning(f"⚠️ 登录页加载网络波动 ({n_err})，等待 3s 重试...")
                page.wait_for_timeout(3000)

        if not nav_ok:
            logger.error("❌ 无法访问登录页，网络链路不稳定")
            return []

        # 检查是否在密码重置后已直接自动登录进控制台
        logged_in = False
        cur_u = page.url.lower()
        if "overview" in cur_u or ("dashboard" in cur_u and "login" not in cur_u and "sign-up" not in cur_u and "reset" not in cur_u):
            logger.info(f"🎉 密码重置后已自动登录进控制台: {page.url}")
            logged_in = True
        else:
            login_url = "https://dashboard.proxyscrape.com/v2/login"
            logger.info(f"🌐 正在访问登录页: {login_url}")
            page.goto(login_url, timeout=35000, wait_until="domcontentloaded")
            page.wait_for_timeout(2500)
            harvester._dismiss_cookie_banner(page)

            # 阶段 1: 点击 [Continue with email] 激活真实输入表单
            cont_btn = page.locator('button:has-text("Continue with email")').first
            if cont_btn.count() > 0 and cont_btn.is_visible():
                logger.info("🚀 点击 [Continue with email] 打开真实登录表单...")
                cont_btn.click(force=True)
                page.wait_for_timeout(2000)

            # 阶段 2: 填入真实表单中的 Email
            logger.info(f"📧 [阶段 2] 正在填入登录邮箱: {email}")
            email_loc = page.locator('input[type="email"], input[name="email"], input[placeholder*="Email" i]').first
            try:
                email_loc.wait_for(state="visible", timeout=12000)
                email_loc.click(timeout=2500)
                email_loc.fill(email, timeout=2500)
            except Exception:
                pass
            page.evaluate("""(em) => {
                const el = document.querySelector('input[type="email"], input[name="email"], input[placeholder*="Email" i]');
                if (el) {
                    el.value = em;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }
            }""", email)
            page.wait_for_timeout(400)

            # 阶段 3: 填入真实表单中的 Password
            logger.info("🔑 [阶段 3] 检测并填入密码...")
            pwd_loc = page.locator('#auth-login-v2-password, input[type="password"], input[name="password"]').first
            try:
                pwd_loc.wait_for(state="visible", timeout=12000)
                pwd_loc.click(timeout=2500)
                pwd_loc.fill(known_password, timeout=2500)
            except Exception:
                pass
            page.evaluate("""(p) => {
                const el = document.querySelector('#auth-login-v2-password, input[type="password"], input[name="password"]');
                if (el) {
                    el.value = p;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }
            }""", known_password)
            page.wait_for_timeout(400)

            # 阶段 4: 等待并求解 Turnstile 激活 Sign In 按钮
            logger.info("⏳ [阶段 4] 等待 Cloudflare Turnstile 验证并激活 [Sign In] 按钮...")
            sign_in_btn = page.locator('button[type="submit"]:has-text("Sign In"), button:has-text("Sign In"), button[type="submit"]').first

            for _w in range(40):
                if sign_in_btn.count() > 0 and not sign_in_btn.is_disabled():
                    logger.info("🎉 [Turnstile] 凭证确权通过，[Sign In] 按钮已点亮激活！")
                    break
                harvester._solve_turnstile_widget(page)
                page.wait_for_timeout(1000)

            logger.info("🚀 正在提交登录表单...")
            if sign_in_btn.count() > 0:
                sign_in_btn.click(force=True, timeout=3000)
            else:
                page.evaluate("() => { const b = document.querySelector('button[type=\"submit\"]'); if (b) b.click(); }")
            page.wait_for_timeout(3500)

            # 阶段 4: 等待跳转进入控制台，或捕获二次 OTP
            for step in range(35):
                page.wait_for_timeout(1500)
                try:
                    cur_url = page.url.lower()
                except Exception:
                    continue

                if "overview" in cur_url or ("dashboard" in cur_url and "login" not in cur_url and "sign-up" not in cur_url and "verify" not in cur_url):
                    logger.info(f"🎉 已成功进入控制台: {page.url}")
                    logged_in = True
                    break

                # 检查 OTP
                try:
                    has_code_ui = page.evaluate("""() => {
                        const text = document.body ? document.body.innerText.toLowerCase() : '';
                        const hasInp = !!document.querySelector('input[placeholder*="code" i], input[placeholder*="verification" i], input[type="text"]');
                        const isVerify = window.location.href.includes('verify');
                        return isVerify || ((text.includes('verification code') || text.includes('enter the code') || text.includes('we sent')) && hasInp);
                    }""")
                except Exception:
                    has_code_ui = False

                if has_code_ui:
                    logger.info("📩 探测到邮箱验证码输入界面，正在从 Outlook Graph API 捕获验证码...")
                    code = dispatcher.wait_for_verification_code(email=email, source_type="outlook", timeout=90)
                    if code:
                        logger.info(f"🔢 成功捕获到登录验证码: {code}")
                        code_loc = page.locator('input[placeholder*="Verification code" i], input[name="code"], input[type="text"]').first
                        if code_loc.count() > 0:
                            code_loc.fill(code)
                            page.wait_for_timeout(300)
                            page.keyboard.press("Enter")
                        vbtn = page.locator('button[type="submit"], button:has-text("Verify"), button:has-text("Continue")').first
                        if vbtn.count() > 0 and vbtn.is_visible():
                            vbtn.click(force=True)
                        page.wait_for_timeout(4000)
                        continue

                # 检查密码错误提示
                err_text = page.evaluate("""() => {
                    const text = document.body ? document.body.innerText : '';
                    if (text.includes('Email or Password is invalid') || text.includes('Account not found')) return 'invalid_credentials';
                    return '';
                }""")
                if err_text:
                    logger.warning(f"⚠️ 检测到凭证错误提示 ('{err_text}')，自动启动密码重置通道...")
                    ok = reset_password_flow(page, harvester, dispatcher, email, DEFAULT_RECOVER_PASSWORD)
                    if ok:
                        logger.info("🎉 密码重置成功，重新发起自动登录...")
                        known_password = DEFAULT_RECOVER_PASSWORD
                        # 重新导航到登录页
                        page.goto("https://dashboard.proxyscrape.com/v2/login", timeout=35000, wait_until="domcontentloaded")
                        page.wait_for_timeout(2500)
                        harvester._dismiss_cookie_banner(page)

                        # Step 1
                        em_l = page.locator('input[type="email"]').first
                        em_l.fill(email)
                        page.wait_for_timeout(300)
                        c_btn = page.locator('button:has-text("Continue with email")').first
                        if c_btn.count() > 0:
                            c_btn.click(force=True)
                        page.wait_for_timeout(2500)

                        # Step 2
                        pw_l = page.locator('#auth-login-v2-password').first
                        pw_l.fill(known_password)
                        page.wait_for_timeout(300)

                        # Step 3: Turnstile
                        sb = page.locator('button[type="submit"]:has-text("Sign In")').first
                        for _ in range(40):
                            if sb.count() > 0 and not sb.is_disabled():
                                break
                            harvester._solve_turnstile_widget(page)
                            page.wait_for_timeout(1000)

                        if sb.count() > 0:
                            sb.click(force=True)
                        page.wait_for_timeout(4000)
                        if "overview" in page.url.lower() or "dashboard" in page.url.lower():
                            logged_in = True
                            break
                    break

        if not logged_in:
            logger.error(f"❌ 未能成功登录至控制台 (当前 URL: {page.url})")
            return []

        # 进入控制台后提取代理
        page.wait_for_timeout(3000)
        harvester._dismiss_cookie_banner(page)
        harvester._dismiss_onboarding_modals(page)
        harvester._claim_free_trial_if_needed(page)

        logger.info("⏳ 正在等待 ProxyScrape 云端完成 100 节点试用开通 (最多等待 120s)...")
        proxies = []
        sub_user = ""
        sub_pass = ""
        max_wait_secs = 120
        poll_start = time.time()
        poll_cnt = 0

        while time.time() - poll_start < max_wait_secs:
            poll_cnt += 1
            harvester._dismiss_cookie_banner(page)
            harvester._dismiss_onboarding_modals(page)
            harvester._claim_free_trial_if_needed(page)

            proxies, sub_user, sub_pass = harvester._fetch_proxies_from_internal_api(page)
            if proxies and len(proxies) > 0:
                elapsed = int(time.time() - poll_start)
                logger.info(f"🎉 [成功提取] 经过 {elapsed}s 等待，成功捕获到 {len(proxies)} 个 100 节点代理！")
                break

            proxies = harvester._extract_proxies(page)
            if proxies and len(proxies) > 0:
                elapsed = int(time.time() - poll_start)
                logger.info(f"🎉 [DOM提取成功] 经过 {elapsed}s 等待，成功捕获到 {len(proxies)} 个 100 节点代理！")
                break

            elapsed = int(time.time() - poll_start)
            logger.info(f"⏳ [云端开通中] 第 {poll_cnt} 次轮询 (已等待 {elapsed}s/120s)，等待 ProxyScrape 配额下发...")

            if poll_cnt in (3, 7, 11, 15):
                target_path = "/v2/datacenter-proxies" if poll_cnt % 2 == 1 else "/v2/overview"
                try:
                    logger.info(f"🔄 正在导航刷新控制台页面以同步配额: {target_path}")
                    page.goto(f"https://dashboard.proxyscrape.com{target_path}", timeout=30000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)
                except Exception as nav_e:
                    logger.warning(f"导航异常: {nav_e}")
            else:
                page.wait_for_timeout(5000)

        if proxies:
            logger.info(f"🎉 [成功] 提取到 {len(proxies)} 个 100 节点代理！")
            clean_email = re.sub(r"[^a-zA-Z0-9]", "_", email)
            out_file = f"F:/github/turb-gpt-free-register/proxies_recovered_{clean_email}_{len(proxies)}.txt"
            with open(out_file, "w", encoding="utf-8") as f:
                f.write("\n".join(proxies) + "\n")
            logger.info(f"📁 节点已保存至: {out_file}")

            rec = ProxyAccountRecord(
                email=email,
                account_password=known_password or "recovered_oauth",
                email_source="outlook",
                sub_user=sub_user or "recovered_user",
                sub_pass=sub_pass or "",
                node_count=len(proxies),
                proxies=proxies,
                batch_id="recovered_20261011",
            )
            save_account_record(rec)
            logger.info("💾 资产已成功持久化至本地 SQLite 数据库！")
            return proxies
        else:
            logger.warning(f"⚠️ 当前控制台提取到的节点为空，当前页面 URL: {page.url}")
            return []

    except Exception as ex:
        logger.error(f"❌ 运行异常: {ex}", exc_info=True)
        return []
    finally:
        try:
            driver.quit()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(description="ProxyScrape 已注册账号一键恢复与节点提取工具")
    parser.add_argument("--email", "-e", default=None, help="目标已注册邮箱 (默认自动扫描所有待恢复账号)")
    parser.add_argument("--all", action="store_true", help="按顺序批量恢复并提取所有已注册邮箱")
    parser.add_argument("--password", "-p", default=None, help="指定账号密码")
    parser.add_argument("--headless", action="store_true", help="以无头模式运行 (默认有头可视化)")
    args = parser.parse_args()

    target_list = []
    if args.email:
        target_list = [args.email.strip()]
    elif args.all:
        target_list = [
            "SavannahCollins19338cW@outlook.com",
            "KarlBrown20832n@outlook.com",
            "RigelArrietty2253@hotmail.com",
            "tanxiulan23426@outlook.com",
            "PaulaJiraiya2895@hotmail.com",
        ]
    else:
        # 默认恢复第一个待提取账号 SavannahCollins
        target_list = ["SavannahCollins19338cW@outlook.com"]

    for idx, em in enumerate(target_list, 1):
        logger.info(f"\n=======================================================")
        logger.info(f"▶ 正在处理 [{idx}/{len(target_list)}]: {em}")
        logger.info(f"=======================================================")
        recover_and_extract(email=em, password=args.password, headed=not args.headless)


if __name__ == "__main__":
    main()
