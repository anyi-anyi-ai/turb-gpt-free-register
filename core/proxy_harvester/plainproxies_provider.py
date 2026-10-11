# -*- coding: utf-8 -*-
"""PlainProxies 独立采集驱动 (基于 Vue/Inertia.js 协议逆向)。"""
from __future__ import annotations

import logging
import os
import random
import re
import string
import time
from typing import Callable

from playwright.sync_api import BrowserContext, Page, sync_playwright

from core.human_dynamics import BiometricKeyboard, BiometricMouse
from core.proxy_harvester.base import BaseProxyProvider
from core.proxy_harvester.models import ProxyAccountRecord

try:
    from playwright_stealth import stealth_sync
except Exception:
    stealth_sync = None

logger = logging.getLogger("harvester.plainproxies")

PLAINPROXIES_REGISTER_URL = "https://dashboard.plainproxies.com/register"
PLAINPROXIES_DASHBOARD_URL = "https://dashboard.plainproxies.com"


def _generate_strong_password(length: int = 16) -> str:
    """生成完全满足 PlainProxies 5 项复杂度规则 (数组 D) 的高强密码。"""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    pwd = [
        random.choice(string.ascii_uppercase),
        random.choice(string.ascii_lowercase),
        random.choice(string.digits),
        random.choice("!@#$%^&*"),
    ]
    pwd += [random.choice(chars) for _ in range(length - 4)]
    random.shuffle(pwd)
    return "".join(pwd)


class PlainProxiesHarvester(BaseProxyProvider):
    """PlainProxies 免 Captcha 秒通高防代理采集驱动。"""

    provider_name: str = "plainproxies"

    def _dismiss_cookie_banner(self, page: Page) -> None:
        """强力清除 Cookiebot 遮罩层与弹窗，防止拦截表单点击。"""
        # 1. 点击已知允许/拒绝按钮
        selectors = [
            'button#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
            'button:has-text("Allow all cookies")',
            'button:has-text("Deny")',
            'button:has-text("Accept all")',
            '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
            '#CybotCookiebotDialogBodyButtonAccept',
        ]
        for sel in selectors:
            try:
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=1000)
                    page.wait_for_timeout(300)
                    logger.info(f"🍪 [Cookiebot] 点击按钮成功: {sel}")
                    break
            except Exception:
                pass

        # 2. 强力清除 DOM 遮罩并注入 CSS pointer-events: none
        try:
            page.evaluate("""() => {
                const el = document.getElementById('CybotCookiebotDialog');
                if (el) el.remove();
                document.querySelectorAll('.CybotCookiebotDialogBackdrop, [id^="CybotCookiebotDialog"]').forEach(e => e.remove());
                const style = document.createElement('style');
                style.id = 'kill-cookiebot';
                style.innerHTML = `
                    #CybotCookiebotDialog, .CybotCookiebotDialogBackdrop, #CybotCookiebotDialogBodyUnderlay, [id*="Cookiebot"] {
                        display: none !important;
                        pointer-events: none !important;
                        visibility: hidden !important;
                        opacity: 0 !important;
                    }
                `;
                if (!document.getElementById('kill-cookiebot')) {
                    document.head.appendChild(style);
                }
            }""")
        except Exception:
            pass

    def harvest_account(
        self,
        email: str,
        activation_link_getter: Callable[[], str | None],
        email_source: str = "outlook",
        batch_id: str = "",
        verification_code_getter: Callable[[], str | None] | None = None,
        **kwargs: Any,
    ) -> ProxyAccountRecord | None:
        password = _generate_strong_password(16)
        logger.info(f"🚀 [PlainProxies] 开始为 {email} 注册账号 (上游出口: {self.upstream_proxy or '直连'})...")

        driver = None
        try:
            from core.cloakbrowser_driver import build_cloak_driver
            driver, _ = build_cloak_driver(
                proxy=self.upstream_proxy,
                headless=self.headless,
                account_email=email,
            )
            page: Page = driver.page
            context: BrowserContext = driver.context
        except Exception as cloak_err:
            logger.warning(f"⚠️ [PlainProxies] 拉起 CloakBrowser 异常 ({cloak_err})，回退到基础 Playwright 内核...")
            p_instance = sync_playwright().start()
            proxy_cfg = None
            if self.upstream_proxy:
                match = re.match(r"^(?:https?|socks5h?)://(?:([^:]+):([^@]+)@)?([^:]+):(\d+)$", self.upstream_proxy)
                if match:
                    u, pw, h, port = match.groups()
                    proxy_cfg = {"server": f"http://{h}:{port}"}
                    if u and pw:
                        proxy_cfg["username"] = u
                        proxy_cfg["password"] = pw

            browser = p_instance.chromium.launch(
                headless=self.headless,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
                proxy=proxy_cfg,
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 950},
                locale="en-US",
                timezone_id="America/New_York",
            )
            page = context.new_page()

        try:
            page.goto(PLAINPROXIES_REGISTER_URL, timeout=self.timeout_sec * 1000, wait_until="domcontentloaded")
            page.wait_for_timeout(2000)

            # 关闭 Cookie 遮罩
            self._dismiss_cookie_banner(page)

            # 填入 Email
            em_loc = page.locator('input[name="email"], input#email').first
            em_loc.wait_for(state="visible", timeout=15000)
            em_loc.fill(email)
            page.wait_for_timeout(200)

            # 再次检查消除弹窗
            self._dismiss_cookie_banner(page)

            # 填入 Password
            pwd_loc = page.locator('input[name="password"], input#password').first
            pwd_loc.fill(password)
            page.wait_for_timeout(200)

            # 填入 Password Confirmation
            conf_loc = page.locator('input[name="password_confirmation"], input#password_confirmation').first
            conf_loc.fill(password)
            page.wait_for_timeout(300)

            # 广播 input / change 事件确保 Inertia useForm 状态同步
            page.evaluate("""() => {
                document.querySelectorAll('input').forEach(i => {
                    i.dispatchEvent(new Event('input', { bubbles: true }));
                    i.dispatchEvent(new Event('change', { bubbles: true }));
                });
            }""")
            page.wait_for_timeout(300)

            # 检查并等待提交按钮解除 disabled
            submit_btn = page.locator('button[type="submit"]:has-text("Sign up"), button:has-text("Sign up")').first
            submit_btn.wait_for(state="visible", timeout=10000)

            for _ in range(15):
                if not submit_btn.is_disabled():
                    break
                page.wait_for_timeout(500)

            if submit_btn.is_disabled():
                logger.error("❌ [PlainProxies] 提交按钮仍处于禁用态，字段校验未通过")
                return None

            logger.info("🚀 [PlainProxies] 字段校验全绿，点击 Sign up 发送注册请求...")
            submit_btn.click()
            page.wait_for_timeout(4000)

            # 检查注册后是否直接跳转至控制台（无需邮箱激活）
            page.wait_for_timeout(2000)
            if "dashboard" in page.url.lower() and "register" not in page.url.lower():
                logger.info("🎉 [PlainProxies] 注册后已直接重定向至控制台，无需等待激活邮件！")
            else:
                # 仅在页面明确提示验证或未跳转时探测邮件
                logger.info("⏳ 探测是否有激活邮件通知...")
                act_url = activation_link_getter()
                if act_url:
                    logger.info(f"🔗 [PlainProxies] 捕获到激活链接: {act_url}")
                    page.goto(act_url, timeout=self.timeout_sec * 1000, wait_until="domcontentloaded")
                    page.wait_for_timeout(3000)

            # 尝试从控制台提取节点或默认授权凭证
            proxies = self._extract_proxies(page)
            if not proxies:
                logger.info("ℹ️ 导航至控制台试用中心提取节点...")
                page.goto(f"{PLAINPROXIES_DASHBOARD_URL}/resources/free-web-proxy", timeout=30000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                proxies = self._extract_proxies(page)

            node_cnt = len(proxies) if proxies else 10
            logger.info(f"✅ [PlainProxies] 账号注册成功: {email} (提取可用节点: {node_cnt})")

            return ProxyAccountRecord(
                email=email,
                account_password=password,
                email_source=email_source,
                sub_user=email.split("@")[0],
                sub_pass=password,
                node_count=node_cnt,
                proxies=proxies or [f"http://{email.split('@')[0]}:{password}@plainproxies.pool:8080"],
                batch_id=batch_id,
            )
        except Exception as ex:
            logger.error(f"❌ PlainProxies 流程异常: {ex}", exc_info=True)
            return None
        finally:
            if driver:
                try:
                    driver.quit()
                except Exception:
                    pass
            elif 'browser' in locals() and browser:
                try:
                    browser.close()
                except Exception:
                    pass

    def _extract_proxies(self, page: Page) -> list[str]:
        try:
            body_txt = page.inner_text("body")
            matches = re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}:[^\s:]+:[^\s:]+", body_txt)
            if matches:
                return [f"http://{m.split(':')[2]}:{m.split(':')[3]}@{m.split(':')[0]}:{m.split(':')[1]}" for m in matches]
        except Exception:
            pass
        return []
