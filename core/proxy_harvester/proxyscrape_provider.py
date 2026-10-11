# -*- coding: utf-8 -*-
"""ProxyScrape 7天独享粘性 100 节点采集驱动。"""
from __future__ import annotations

import logging
import os
import random
import re
import string
import time
from pathlib import Path
from typing import Callable

from playwright.sync_api import BrowserContext, Page, sync_playwright

from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile
from core.human_dynamics import BiometricKeyboard, BiometricMouse
from core.proxy_harvester.base import BaseProxyProvider
from core.proxy_harvester.models import ProxyAccountRecord

try:
    from playwright_stealth import stealth_sync
except Exception:
    stealth_sync = None

logger = logging.getLogger("harvester.proxyscrape")

PROXYSCRAPE_REGISTER_URL = "https://dashboard.proxyscrape.com/v2/sign-up"
PROXYSCRAPE_DASHBOARD_URL = "https://dashboard.proxyscrape.com"


def _generate_strong_password(length: int = 16) -> str:
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


class ProxyScrapeHarvester(BaseProxyProvider):
    """ProxyScrape 7天锁定 100 节点 / 10GB 流量驱动。"""

    provider_name: str = "proxyscrape"

    def _safe_fill_input(self, page: Page, selector: str, value: str, field_name: str = "Input") -> bool:
        """双重保障填表：优先 Playwright fill + DOM 校验 + JS 原生原型链 setter 兜底。"""
        try:
            page.wait_for_selector(selector, state="attached", timeout=45000)
            loc = page.locator(selector).first
            try:
                loc.scroll_into_view_if_needed()
                loc.click(force=True, timeout=2000)
            except Exception:
                pass
            page.wait_for_timeout(100)

            try:
                loc.fill(value, timeout=2000)
            except Exception:
                pass
            page.wait_for_timeout(100)

            # 强校验与底层 JS prototype setter 兜底 (免受遮罩拦截)
            page.evaluate(
                """([sel, val]) => {
                    const el = document.querySelector(sel);
                    if (!el) return;
                    if (el.value === val) return;
                    const descriptor = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value');
                    if (descriptor && descriptor.set) {
                        descriptor.set.call(el, val);
                    } else {
                        el.value = val;
                    }
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                    el.dispatchEvent(new Event('blur', { bubbles: true }));
                }""",
                [selector, value],
            )
            page.wait_for_timeout(150)

            final_val = page.evaluate("sel => document.querySelector(sel)?.value", selector)
            return final_val == value
        except Exception:
            return False

    def harvest_account(
        self,
        email: str,
        activation_link_getter: Callable[[], str | None] | None = None,
        email_source: str = "outlook",
        batch_id: str = "",
        verification_code_getter: Callable[[], str | None] | None = None,
    ) -> ProxyAccountRecord | None:
        password = _generate_strong_password(16)
        logger.info(f"🚀 [ProxyScrape] 开始为 {email} 注册账号 (上游出口: {self.upstream_proxy or '直连'})...")

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
            logger.warning(f"⚠️ [ProxyScrape] 拉起 CloakBrowser 异常 ({cloak_err})，回退到基础 Playwright 内核...")
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
            nav_ok = False
            for nav_idx in range(3):
                try:
                    logger.info(f"🌐 正在访问 ProxyScrape 注册页面 (第 {nav_idx+1}/3 次尝试)...")
                    page.goto(PROXYSCRAPE_REGISTER_URL, timeout=60000, wait_until="domcontentloaded")
                    nav_ok = True
                    break
                except Exception as n_err:
                    logger.warning(f"⚠️ 访问页面遇到网络波动 ({n_err})，等待 2.5s 重试...")
                    page.wait_for_timeout(2500)

            if not nav_ok:
                logger.error("❌ 多次尝试无法加载 ProxyScrape 注册页，当前代理链路不稳定")
                return None

            page.wait_for_timeout(2500)

            # 关闭 Cookie 遮罩层，避免阻挡 Turnstile 验证框
            self._dismiss_cookie_banner(page)

            self._handle_cf(page)

            # 填表: 邮箱与密码 (双重保障: Playwright fill + DOM 校验 + 原型链 setter 兜底，免受 iframe 遮罩阻断)
            email_sel = 'input[type="email"], input[name="email"], #auth-login-v2-email'
            if not self._safe_fill_input(page, email_sel, email, "Email"):
                logger.error(f"❌ [ProxyScrape] 邮箱填写失败，终止当前账号！")
                return None
            page.wait_for_timeout(200)

            pwd_sel = '#auth-login-v2-password, input[name="password"], input[type="password"]'
            if not self._safe_fill_input(page, pwd_sel, password, "Password"):
                logger.error(f"❌ [ProxyScrape] 密码填写失败，终止当前账号！")
                return None
            page.wait_for_timeout(150)

            confirm_pwd_sel = '#auth-login-v2-confirm-password, input[name="confirmPassword"]'
            if not self._safe_fill_input(page, confirm_pwd_sel, password, "ConfirmPassword"):
                logger.error(f"❌ [ProxyScrape] 确认密码填写失败，终止当前账号！")
                return None
            page.wait_for_timeout(200)

            # 勾选条款 (使用 Playwright 拟人点击 + React Fiber 受控状态双重确权)
            try:
                terms_cb = page.locator('.MuiCheckbox-root, label:has(input[type="checkbox"]), input[type="checkbox"]').first
                if terms_cb.count() > 0:
                    terms_cb.scroll_into_view_if_needed()
                    terms_cb.click(force=True)
                page.wait_for_timeout(200)

                # 确保 React Fiber 内部状态机 F (termsAccepted) 100% 设为 true
                page.evaluate("""() => {
                    const root = document.querySelector('#__next') || document.body;
                    let rootFiber = null;
                    for (const k in root) {
                        if (k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$')) {
                            rootFiber = root[k];
                            break;
                        }
                    }
                    const queue = [rootFiber];
                    while (queue.length > 0) {
                        const node = queue.shift();
                        if (!node) continue;
                        if (node.type && (node.type.name === 'F' || (typeof node.type === 'function' && String(node.type).includes('cf_turnstile_token')))) {
                            let h = node.memoizedState;
                            let idx = 0;
                            while (h) {
                                if (idx === 3 && h.queue && h.queue.dispatch) {
                                    h.queue.dispatch(true);
                                    break;
                                }
                                idx++;
                                h = h.next;
                            }
                            break;
                        }
                        if (node.child) queue.push(node.child);
                        if (node.sibling) queue.push(node.sibling);
                    }
                    const cb = document.querySelector('input[type="checkbox"]');
                    if (cb && !cb.checked) cb.click();
                }""")
                logger.info("✅ 已确认条款复选框勾选 (React F=true)")
            except Exception as ex:
                logger.debug(f"勾选条款异常: {ex}")
            page.wait_for_timeout(300)

            # 校验表单错误
            form_errs = page.evaluate("""() => {
                return Array.from(document.querySelectorAll('.MuiFormHelperText-root.Mui-error')).map(e => e.innerText.trim()).filter(Boolean);
            }""")
            if form_errs:
                logger.warning(f"⚠️ [ProxyScrape] 表单字段校验提示: {form_errs}")
            else:
                logger.info("✅ [ProxyScrape] 表单所有字段 (邮箱/密码/确认密码/条款) 校验完全通过！")

            # 提交前严格校验
            cur_em = page.locator('input[type="email"]').first.input_value()
            if not cur_em or "@" not in cur_em:
                logger.error(f"❌ [ProxyScrape] 邮箱校验未通过 ('{cur_em}')，终止提交！")
                return None

            # 强制失焦，确保 Material-UI 校验和 DOM 完全收敛，避免失焦导致表单布局产生 19px 错位
            page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
            page.wait_for_timeout(300)

            # 滚动网页与内部容器到底部，确保 Cloudflare 验证框进入视口触发渲染
            page.evaluate("""() => {
                window.scrollTo(0, document.body.scrollHeight);
                document.querySelectorAll('*').forEach(el => {
                    if (el.scrollHeight > el.clientHeight + 20) {
                        el.scrollTop = el.scrollHeight;
                    }
                });
            }""")
            page.wait_for_timeout(1500)

            # 首次尝试定位并点击 Cloudflare Turnstile 验证框
            self._solve_turnstile_widget(page)

            if not self.headless:
                logger.info("💡 [有头可视化] 页面已自动滚动到底部。如出现 'Verify you are human'，系统将以拟人轨迹自动求解，您也可在视窗中点击！")

            # 定位提交按钮并智能等待其解除 disabled 状态 (结合 React Fiber 状态机 + Shadow DOM 凭证捕获)
            submit = page.locator('button[type="submit"]:has-text("Continue"), button:has-text("Continue")').first
            logger.info("⏳ 正在等待 Cloudflare 人机验证通过，激活 [Continue] 按钮...")
            btn_active = False
            last_turnstile_click_time = time.time()
            max_wait_cycles = 90 if not self.headless else 45
            for _w in range(max_wait_cycles):
                # 核心轮询探针：自检 React Fiber (F/G/H 状态)、Shadow DOM input 以及 window.turnstile
                probe_res = page.evaluate("""() => {
                    const root = document.querySelector('#__next') || document.body;
                    let rootFiber = null;
                    for (const k in root) {
                        if (k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$')) {
                            rootFiber = root[k];
                            break;
                        }
                    }
                    const queue = [rootFiber];
                    let targetNode = null;
                    while (queue.length > 0) {
                        const node = queue.shift();
                        if (!node) continue;
                        if (node.type && (node.type.name === 'F' || (typeof node.type === 'function' && String(node.type).includes('cf_turnstile_token')))) {
                            targetNode = node;
                            break;
                        }
                        if (node.child) queue.push(node.child);
                        if (node.sibling) queue.push(node.sibling);
                    }

                    let fHooks = null;
                    let setTerms = null;
                    let setTurnstile = null;
                    if (targetNode && targetNode.memoizedState) {
                        let h = targetNode.memoizedState;
                        let idx = 0;
                        const vals = [];
                        while (h) {
                            vals.push(h.memoizedState);
                            if (idx === 3 && h.queue) setTerms = h.queue.dispatch;
                            if (idx === 4 && h.queue) setTurnstile = h.queue.dispatch;
                            idx++;
                            h = h.next;
                        }
                        fHooks = {
                            terms_F: vals[3],
                            turnstile_G: vals[4],
                            submitting_H: vals[5]
                        };
                    }

                    // 1. 确保条款在 React 状态机中始终为 true
                    if (fHooks && fHooks.terms_F !== true && setTerms) {
                        setTerms(true);
                    }
                    const cb = document.querySelector('input[type="checkbox"]');
                    if (cb && !cb.checked) {
                        cb.click();
                    }

                    // 2. 深度穿透探测 Turnstile Token
                    let token = (fHooks && typeof fHooks.turnstile_G === 'string' && fHooks.turnstile_G.length > 10) ? fHooks.turnstile_G : '';

                    // 穿透检索所有 Shadow DOM
                    if (!token) {
                        function findInShadow(r) {
                            if (!r) return null;
                            const el = r.querySelector && r.querySelector('[name="cf-turnstile-response"]');
                            if (el && el.value && el.value.length > 10) return el.value;
                            const all = r.querySelectorAll ? r.querySelectorAll('*') : [];
                            for (const child of all) {
                                if (child.shadowRoot) {
                                    const res = findInShadow(child.shadowRoot);
                                    if (res) return res;
                                }
                            }
                            return null;
                        }
                        token = findInShadow(document.body) || '';
                    }

                    // 检索标准 DOM
                    if (!token) {
                        const domInp = document.querySelector('[name="cf-turnstile-response"], [name="g-recaptcha-response"]');
                        if (domInp && domInp.value && domInp.value.length > 10) token = domInp.value;
                    }
                    if (!token) {
                        for (const input of document.querySelectorAll('input[type="hidden"]')) {
                            if (input.value && input.value.length > 20 && (input.name.includes('turnstile') || input.name.includes('cf') || input.id.includes('cf'))) {
                                token = input.value;
                                break;
                            }
                        }
                    }

                    // 检索 window.turnstile
                    if (!token && window.turnstile && typeof window.turnstile.getResponse === 'function') {
                        try {
                            const resp = window.turnstile.getResponse();
                            if (resp && resp.length > 10) token = resp;
                        } catch(e) {}
                    }

                    // 3. 若已探测到 Token 但 React Fiber 中未更新，立即向 React 派发同步！
                    if (token && fHooks && (!fHooks.turnstile_G || fHooks.turnstile_G.length < 10) && setTurnstile) {
                        setTurnstile(token);
                    }

                    const btn = document.querySelector('button[type="submit"]') || Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Continue'));
                    const isBtnDisabled = btn ? (btn.disabled || btn.classList.contains('Mui-disabled')) : true;

                    return {
                        terms_F: fHooks ? fHooks.terms_F : null,
                        turnstile_G: fHooks ? fHooks.turnstile_G : null,
                        token: token,
                        btnDisabled: isBtnDisabled,
                        tokenLen: token ? token.length : 0
                    };
                }""")

                # 检查 Playwright Frame 层面是否存在 Token
                cur_token = probe_res.get("token") or ""
                if not cur_token:
                    for frame in page.frames:
                        try:
                            f_val = frame.evaluate("() => document.querySelector('[name=\"cf-turnstile-response\"]')?.value")
                            if f_val and len(f_val) > 10:
                                cur_token = f_val
                                logger.info(f"🎉 从 Frame 探测到 Turnstile Token (len={len(f_val)})，同步至 React 状态机！")
                                page.evaluate("""(tok) => {
                                    const root = document.querySelector('#__next') || document.body;
                                    let rootFiber = null;
                                    for (const k in root) {
                                        if (k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$')) {
                                            rootFiber = root[k];
                                            break;
                                        }
                                    }
                                    const queue = [rootFiber];
                                    while (queue.length > 0) {
                                        const node = queue.shift();
                                        if (!node) continue;
                                        if (node.type && (node.type.name === 'F' || (typeof node.type === 'function' && String(node.type).includes('cf_turnstile_token')))) {
                                            let h = node.memoizedState;
                                            let idx = 0;
                                            while (h) {
                                                if (idx === 4 && h.queue && h.queue.dispatch) {
                                                    h.queue.dispatch(tok);
                                                    break;
                                                }
                                                idx++;
                                                h = h.next;
                                            }
                                            break;
                                        }
                                        if (node.child) queue.push(node.child);
                                        if (node.sibling) queue.push(node.sibling);
                                    }
                                }""", f_val)
                                break
                        except Exception:
                            pass

                # 条件满足：按钮解禁或凭证已到位
                if not probe_res.get("btnDisabled") or len(cur_token) > 10:
                    logger.info(f"🎉 [Turnstile] 凭证确权完全通过 (Token len={len(cur_token)}), 点亮并激活 [Continue] 按钮！")
                    btn_active = True
                    break

                # 实时检测 Cloudflare Turnstile 是否由于 IP/环境风控抛出 'Verification failed'
                has_cf_failure = False
                for frame in page.frames:
                    if frame == page.main_frame:
                        continue
                    f_url = str(getattr(frame, "url", "") or "")
                    if "cookiebot" in f_url:
                        continue
                    try:
                        f_info = frame.evaluate("""() => {
                            let allText = '';
                            let hasFailIcon = false;
                            function scan(node) {
                                if (!node) return;
                                if (node.innerText) allText += ' ' + node.innerText.toLowerCase();
                                if (node.textContent) allText += ' ' + node.textContent.toLowerCase();
                                if (node.querySelector) {
                                    if (node.querySelector('#fail, .cf-error, [aria-label*="fail" i], svg[class*="fail" i]')) hasFailIcon = true;
                                }
                                if (node.shadowRoot) scan(node.shadowRoot);
                                if (node.children) {
                                    for (const c of node.children) scan(c);
                                }
                            }
                            scan(document.body);
                            const isFailText = ['échec', 'echec', 'failed', 'failure', 'fehlgeschlagen', 'fallida', 'falha', 'epäonnistui', 'сбой', '失败', '失敗'].some(w => allText.includes(w));
                            return hasFailIcon || isFailText;
                        }""")
                        if f_info:
                            has_cf_failure = True
                            break
                    except Exception:
                        pass
                if has_cf_failure:
                    logger.error("❌ [Turnstile] 检测到 Cloudflare 报 'Verification failed' 风险熔断 (当前出口 IP 被标记或环境受限)，立即换号换 IP！")
                    return None

                # 智能状态机：若尚未获取 Token，自检并适时激活待勾选复选框 (引入12s冷却，杜绝连击打断质询计算)
                if not cur_token:
                    now = time.time()
                    if now - last_turnstile_click_time > 12.0:
                        if self._solve_turnstile_widget(page):
                            last_turnstile_click_time = now

                page.wait_for_timeout(1000)

            if not btn_active:
                logger.error(f"❌ 等待 Cloudflare 验证通过超时 ({max_wait_cycles}s)，[Continue] 按钮未点亮，终止当前账号")
                return None

            # 提交前解禁按钮 DOM 属性与样式
            page.evaluate("""() => {
                const b = document.querySelector('button[type="submit"]') || Array.from(document.querySelectorAll('button')).find(el => el.innerText.includes('Continue'));
                if (b) {
                    b.removeAttribute('disabled');
                    b.classList.remove('Mui-disabled');
                }
            }""")
            page.wait_for_timeout(200)

            # 禁用悬浮/广告 iframe 的 pointer-events，防止阻挡点击
            page.evaluate("() => document.querySelectorAll('iframe[src*=\"intercom\"], iframe[src*=\"cookiebot\"]').forEach(i => i.style.pointerEvents = 'none')")
            page.wait_for_timeout(200)

            submit_success = False
            try:
                submit = page.locator('button[type="submit"]:has-text("Continue"), button:has-text("Continue")').first
                if submit.count() > 0:
                    submit.scroll_into_view_if_needed()
                    page.wait_for_timeout(200)
                    submit.click(force=True, timeout=3000)
                    submit_success = True
            except Exception as click_err:
                logger.warning(f"⚠️ [ProxyScrape] force点击被拦截 ({click_err})，自动回退到原生 DOM 提交...")

            if not submit_success:
                try:
                    page.evaluate("""() => {
                        const btn = document.querySelector('button[type="submit"]') || Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Continue'));
                        if (btn) {
                            btn.removeAttribute('disabled');
                            btn.classList.remove('Mui-disabled');
                            const form = btn.closest('form');
                            if (form && typeof form.requestSubmit === 'function') {
                                form.requestSubmit(btn);
                            } else {
                                btn.click();
                            }
                        }
                    }""")
                    submit_success = True
                except Exception as eval_err:
                    logger.error(f"❌ DOM 强制提交失败: {eval_err}")

            logger.info("🚀 [Continue] 按钮已激活并成功触发提交，等待注册响应...")
            page.wait_for_timeout(3500)

            # 智能等待并判断页面进入确权模式：
            # 1. 跳转至 /v2/verify 需要输入邮箱验证码
            # 2. LocalStorage 获取到 accessToken
            # 3. 直接进入控制台
            logger.info("⏳ 表单提交完成，等待页面确权响应...")
            is_verify_page = False
            for _iter in range(25):
                cur_url = ""
                try:
                    cur_url = page.url.lower()
                except Exception:
                    pass
                if "/v2/verify" in cur_url or "verify" in cur_url:
                    is_verify_page = True
                    break

                try:
                    has_code_input = page.evaluate("""() => {
                        const text = document.body ? document.body.innerText : '';
                        const hasInput = !!document.querySelector('input[placeholder*="Verification code" i], input[type="text"]');
                        const hasToken = !!window.localStorage.getItem('accessToken');
                        return text.includes('Verify your email') || (hasInput && text.includes('verification code')) || hasToken;
                    }""")
                    if has_code_input:
                        is_verify_page = True
                        break
                except Exception as eval_e:
                    logger.debug(f"[ProxyScrape] 页面跳转渲染中，等待上下文稳定: {eval_e}")

                # 检查页面是否弹出注册错误提示 (如邮箱已占用、地区受限等)
                try:
                    err_msg = page.evaluate("""() => {
                        const errEls = Array.from(document.querySelectorAll('.MuiFormHelperText-root.Mui-error, .MuiAlert-message, [role="alert"]'));
                        return errEls.map(e => e.innerText.trim()).filter(Boolean).join('; ');
                    }""")
                    if err_msg:
                        logger.warning(f"⚠️ [ProxyScrape] 表单提交返回提示: {err_msg}")
                        if any(k in err_msg.lower() for k in ("already", "exists", "registered", "taken", "in use")):
                            logger.error(f"❌ [ProxyScrape] 邮箱已被注册，立即终止当前账号换号")
                            try:
                                with sqlite3.connect(self.db_path or "turb.sqlite3") as _c:
                                    _c.execute(
                                        "INSERT OR IGNORE INTO harvested_proxy_accounts (batch_id, email, email_source, account_password, sub_user, sub_pass, node_count, proxies_json, status, created_at, expires_at) "
                                        "VALUES (?, ?, ?, ?, '', '', 0, '[]', 'already_registered', datetime('now'), datetime('now', '+7 days'))",
                                        (batch_id or 'pre_existing', email, email_source, password)
                                    )
                                    _c.commit()
                            except Exception:
                                pass
                            return None
                        if any(k in err_msg.lower() for k in ("service not available", "not available in your region")):
                            logger.error(f"❌ [ProxyScrape] 当前出口 IP 被判定为限制区域 (Service Not Available)，换号换 IP 重试")
                            return None
                except Exception:
                    pass

                if "overview" in cur_url or "onboarding" in cur_url:
                    break

                # 若仍停留在 sign-up 且无报错，每隔几秒补点一次 Continue
                if "sign-up" in cur_url and _iter in (4, 8, 12, 18):
                    try:
                        page.evaluate("""() => {
                            const btn = document.querySelector('button[type="submit"]') || Array.from(document.querySelectorAll('button')).find(b => b.innerText.includes('Continue'));
                            if (btn) {
                                btn.removeAttribute('disabled');
                                btn.classList.remove('Mui-disabled');
                                const form = btn.closest('form');
                                if (form && typeof form.requestSubmit === 'function') {
                                    form.requestSubmit(btn);
                                } else {
                                    btn.click();
                                }
                            }
                        }""")
                    except Exception:
                        pass

                page.wait_for_timeout(1000)

            if is_verify_page:
                logger.info("📩 [ProxyScrape] 检测到邮箱验证码输入页面，准备提取验证码...")
                code = None
                if verification_code_getter:
                    code = verification_code_getter()
                if not code and activation_link_getter:
                    logger.info("⏳ 尝试从邮件通道兜底探测验证码/链接...")
                    code = activation_link_getter()

                if not code:
                    logger.error("❌ 未能获取到 ProxyScrape 邮箱验证码")
                    return None

                logger.info(f"🔢 [ProxyScrape] 成功捕获到验证码: {code}，正在填入并确权...")

                # 恢复 iframe pointer-events
                page.evaluate("() => document.querySelectorAll('iframe').forEach(i => i.style.pointerEvents = 'auto')")
                page.wait_for_timeout(300)

                # 填写验证码
                code_loc = page.locator('input[placeholder*="Verification code" i], input[name="code"], input[type="text"]').first
                try:
                    code_loc.wait_for(state="visible", timeout=10000)
                    code_loc.scroll_into_view_if_needed()
                    code_loc.click()
                    page.wait_for_timeout(200)
                    code_loc.fill("")
                    for ch in code.strip():
                        page.keyboard.type(ch, delay=random.randint(15, 35))
                    page.wait_for_timeout(300)
                    page.keyboard.press("Tab")
                    page.wait_for_timeout(300)
                except Exception as fill_code_err:
                    logger.warning(f"⚠️ [ProxyScrape] 拟人输入验证码异常 ({fill_code_err})，尝试 DOM 原生赋值...")
                    page.evaluate("""(c) => {
                        const inp = document.querySelector('input[placeholder*="Verification code" i], input[type="text"]');
                        if (inp) {
                            inp.value = c;
                            inp.dispatchEvent(new Event('input', { bubbles: true }));
                            inp.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""", code.strip())
                    page.wait_for_timeout(500)

                # 点击 Verify email 按钮提交
                verify_btn = page.locator('button[type="submit"]:has-text("Verify email"), button:has-text("Verify email")').first
                verify_clicked = False
                try:
                    if verify_btn.count() > 0 and verify_btn.is_visible():
                        verify_btn.scroll_into_view_if_needed()
                        page.wait_for_timeout(200)
                        verify_btn.click(force=True, timeout=4000)
                        verify_clicked = True
                except Exception:
                    pass

                if not verify_clicked:
                    page.evaluate("""() => {
                        const b = document.querySelector('button[type="submit"]');
                        if (b) b.click();
                    }""")

                logger.info("🚀 [ProxyScrape] 验证码已提交，等待确权通过进入控制台...")
                page.wait_for_timeout(4000)
            else:
                # 兜底情况：优先使用验证码探测，兼顾链接
                logger.info("📩 [ProxyScrape] 探测是否有邮箱验证通知...")
                code = verification_code_getter() if verification_code_getter else None
                if code:
                    logger.info(f"🔢 [ProxyScrape] 捕获到验证码: {code}")
                    code_loc = page.locator('input[placeholder*="Verification code" i], input[type="text"]').first
                    if code_loc.count() > 0 and code_loc.is_visible():
                        code_loc.fill(code)
                        page.keyboard.press("Enter")
                elif activation_link_getter:
                    act_url = activation_link_getter()
                    if act_url:
                        logger.info(f"🔗 [ProxyScrape] 捕获到激活链接: {act_url}")
                        page.goto(act_url, timeout=self.timeout_sec * 1000, wait_until="domcontentloaded")
                page.wait_for_timeout(3000)
                self._handle_cf(page)

            # 1. 充分等待进入控制台 (最多 45 秒)
            entered_dashboard = False
            for _ in range(45):
                cur_u = page.url.lower()
                if "dashboard" in cur_u and "sign-up" not in cur_u and "verify" not in cur_u:
                    logger.info(f"🎉 [ProxyScrape] 已成功登入控制台: {page.url}")
                    entered_dashboard = True
                    break
                # 若仍在验证码界面且出现确认按钮，尝试辅助触发
                try:
                    vf = page.locator('button[type="submit"]:has-text("Verify"), button:has-text("Verify")').first
                    if vf.count() > 0 and vf.is_visible():
                        vf.click(force=True, timeout=1000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)

            # 无论节点是否立刻生成，先行持久化账号密码防闪退
            from core.proxy_harvester.models import save_account_record
            save_account_record(ProxyAccountRecord(
                email=email,
                account_password=password,
                email_source=email_source,
                sub_user="",
                sub_pass="",
                node_count=0,
                proxies=[],
                batch_id=batch_id,
            ))
            logger.info(f"💾 [凭证固化] 账号与密码已即刻安全落盘 SQLite: {email}")

            # 2. 深度试用开通与 100 节点长轮询提取 (最多等待 120 秒)
            logger.info("⏳ 正在等待 ProxyScrape 云端完成 100 节点试用开通 (最多等待 120s)...")
            proxies = []
            sub_user = ""
            sub_pass = ""
            max_wait_secs = 120
            poll_start = time.time()
            poll_cnt = 0

            while time.time() - poll_start < max_wait_secs:
                poll_cnt += 1
                self._dismiss_onboarding_modals(page)
                self._dismiss_cookie_banner(page)
                self._claim_free_trial_if_needed(page)

                # 优先调用内部 API 提取
                proxies, sub_user, sub_pass = self._fetch_proxies_from_internal_api(page)
                if proxies and len(proxies) > 0:
                    elapsed = int(time.time() - poll_start)
                    logger.info(f"🎉 [成功提取] 经过 {elapsed}s 等待，成功捕获到 {len(proxies)} 个有效粘性节点！")
                    break

                # 备用：从页面 DOM 提取
                proxies = self._extract_proxies(page)
                if proxies and len(proxies) > 0:
                    elapsed = int(time.time() - poll_start)
                    logger.info(f"🎉 [DOM提取成功] 经过 {elapsed}s 等待，成功捕获到 {len(proxies)} 个有效粘性节点！")
                    break

                elapsed = int(time.time() - poll_start)
                logger.info(f"⏳ [云端试用分配中] 第 {poll_cnt} 次轮询 (已等待 {elapsed}s/120s)，等待 ProxyScrape 配额下发...")

                # 关键：每隔几轮主动导航到专属页面触发后端数据同步
                if poll_cnt in (3, 7, 11, 15):
                    target_path = "/v2/datacenter-proxies" if poll_cnt % 2 == 1 else "/v2/overview"
                    try:
                        logger.info(f"🔄 正在导航刷新控制台页面以同步配额: {target_path}")
                        page.goto(f"{PROXYSCRAPE_DASHBOARD_URL}{target_path}", timeout=30000, wait_until="domcontentloaded")
                        page.wait_for_timeout(3000)
                    except Exception as nav_e:
                        logger.warning(f"页面导航同步异常: {nav_e}")
                else:
                    page.wait_for_timeout(5000)

            if not proxies:
                logger.warning(f"⚠️ ProxyScrape 100 节点试用开通仍在排队中，已安全保留账号凭证: {email}")
                proxies = []

            logger.info(f"✅ [结算] 提取到 ProxyScrape {len(proxies)} 个 7天粘性节点")

            # 若未从 API 获取到凭证，从代理字符串解析
            if proxies and (not sub_user or not sub_pass):
                m = re.match(r"https?://([^:]+):([^@]+)@", proxies[0])
                if m:
                    sub_user, sub_pass = m.groups()

            return ProxyAccountRecord(
                email=email,
                account_password=password,
                email_source=email_source,
                sub_user=sub_user,
                sub_pass=sub_pass,
                node_count=len(proxies),
                proxies=proxies,
                batch_id=batch_id,
            )
        except Exception as ex:
            logger.error(f"❌ ProxyScrape 流程异常: {ex}", exc_info=True)
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

    def _dismiss_cookie_banner(self, page: Page) -> None:
        """关闭 Cookiebot 遮罩层，防止拦截表单点击与遮挡 Turnstile。"""
        try:
            cb_btn = page.locator(
                '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll, '
                '#CybotCookiebotDialogBodyButtonAccept, '
                '.CybotCookiebotDialogBodyButton, '
                'button:has-text("Allow all cookies"), '
                'button:has-text("Accept all")'
            ).first
            if cb_btn.count() > 0 and cb_btn.is_visible():
                cb_btn.click(timeout=2500)
                page.wait_for_timeout(500)
                logger.info("🍪 [Cookiebot] 已点击关闭 ProxyScrape Cookie 授权弹窗遮罩")
        except Exception:
            pass

        try:
            page.evaluate("""() => {
                const s = document.getElementById('kill-interfering-overlays') || document.createElement('style');
                s.id = 'kill-interfering-overlays';
                s.textContent = `
                    #CybotCookiebotDialog, .CybotCookiebotDialogBackdrop, #CybotCookiebotDialogBodyUnderlay,
                    #intercom-container, [class*="intercom"], iframe[title*="Intercom"],
                    iframe[src*="cookiebot"], iframe[src*="intercom"] {
                        pointer-events: none !important;
                        display: none !important;
                        visibility: hidden !important;
                        z-index: -9999 !important;
                    }
                    iframe[src*="cloudflare"], iframe[src*="challenge"], [id*="cf-chl"], [class*="turnstile"] {
                        pointer-events: auto !important;
                        visibility: visible !important;
                        opacity: 1 !important;
                    }
                `;
                if (!document.getElementById('kill-interfering-overlays') && document.head) {
                    document.head.appendChild(s);
                }
                document.querySelectorAll('#CybotCookiebotDialog, .CybotCookiebotDialogBackdrop, #intercom-container, [class*="intercom"], iframe[title*="Intercom"], iframe[src*="cookiebot"]').forEach(el => el.remove());
            }""")
        except Exception:
            pass

    def _dismiss_onboarding_modals(self, page: Page) -> None:
        """关闭任何新用户引导、弹窗或提示框。"""
        try:
            for sel in (
                'button:has-text("Skip")',
                'button:has-text("Dismiss")',
                'button:has-text("Close")',
                'button[aria-label="close"]',
                'button:has-text("Later")',
                'button:has-text("Got it")',
                'button:has-text("Accept")',
            ):
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(timeout=1500)
                    page.wait_for_timeout(300)
        except Exception:
            pass

    def _claim_free_trial_if_needed(self, page: Page) -> None:
        """若出现免费试用申领/激活按钮，自动触发申领。"""
        try:
            for sel in (
                'button:has-text("Start free trial")',
                'button:has-text("Claim free trial")',
                'button:has-text("Activate free trial")',
                'button:has-text("Get free trial")',
                'a:has-text("Start free trial")',
            ):
                btn = page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    logger.info(f"🎁 [ProxyScrape] 探测到试用申领按钮 ({sel})，自动点击领取...")
                    btn.click(timeout=2000)
                    page.wait_for_timeout(2000)
        except Exception:
            pass

    def _fetch_proxies_from_internal_api(self, page: Page) -> tuple[list[str], str, str]:
        """尝试通过前端已登录态与 JWT Token 直接调用 ProxyScrape 内部 API 获取节点与认证凭据。"""
        try:
            api_res = page.evaluate("""async () => {
                const token = window.localStorage.getItem('accessToken');
                if (!token) return { error: 'no_token' };
                const headers = {
                    'Authorization': `Bearer ${token}`,
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                };
                try {
                    // 1. 触发免费试用申领 (POST /v2/v4/account/premium/claim-trial)
                    try {
                        await fetch('/v2/v4/account/premium/claim-trial', {
                            method: 'POST',
                            headers,
                            body: JSON.stringify({}),
                        });
                    } catch (e) {}

                    // 2. 自动提交并跳过新用户引导 (POST /v2/v4/account/onboarding)
                    try {
                        await fetch('/v2/v4/account/onboarding', {
                            method: 'POST',
                            headers,
                            body: JSON.stringify({ useCase: 'Web Scraping', role: 'Developer' }),
                        });
                    } catch (e) {}

                    // 3. 轮询获取用户信息并精准提取真正的 subAccountId (优先从 associatedSubaccounts 数组提取)
                    let subId = null;
                    let meData = null;
                    for (let iter = 0; iter < 8; iter++) {
                        try {
                            const meRes = await fetch('/v2/v4/account/auth/me', {
                                method: 'POST',
                                headers,
                                body: JSON.stringify([]),
                            });
                            if (meRes.ok) {
                                meData = await meRes.json();
                                if (meData && Array.isArray(meData.associatedSubaccounts) && meData.associatedSubaccounts.length > 0) {
                                    const dcSub = meData.associatedSubaccounts.find(s => s && (s.AccountType === 'datacenter_shared' || s.AccountType === 'premium'));
                                    const targetSub = dcSub || meData.associatedSubaccounts[0];
                                    if (targetSub) {
                                        subId = targetSub.AccountID || targetSub.id || targetSub.subAccountId;
                                        if (subId) break;
                                    }
                                }
                                if (meData && meData.currentAccount && meData.currentAccount !== meData.id) {
                                    subId = meData.currentAccount;
                                    break;
                                }
                            }
                        } catch (e) {}
                        await new Promise(r => setTimeout(r, 1200));
                    }

                    // 4. 兜底尝试 subaccounts 独立查询接口
                    if (!subId) {
                        try {
                            const subRes = await fetch('/v2/v4/account/subaccounts', {
                                headers: { 'Authorization': `Bearer ${token}` }
                            });
                            if (subRes.ok) {
                                const subData = await subRes.json();
                                if (Array.isArray(subData) && subData.length > 0) {
                                    subId = subData[0].AccountID || subData[0].id || subData[0].subAccountId;
                                }
                            }
                        } catch (e) {}
                    }

                    if (!subId) {
                        return { error: 'no_subaccount_found', me: meData };
                    }

                    // 5. 提取代理用户名与密码凭据 (重置凭证)
                    let subUser = '';
                    let subPass = '';
                    try {
                        const cRes = await fetch(`/v2/v4/account/${subId}/datacenter_shared/credentials/reset`, {
                            method: 'POST',
                            headers,
                            body: JSON.stringify({}),
                        });
                        if (cRes.ok) {
                            const cData = await cRes.json();
                            subUser = cData.proxy_username || '';
                            subPass = cData.proxy_password || '';
                        }
                    } catch (e) {}

                    // 6. 提取代理列表 (必须携带 protocol=http 或 protocol=all 参数)
                    const protoList = ['http', 'all', 'socks5'];
                    for (const proto of protoList) {
                        try {
                            const pRes = await fetch(`/v2/v4/account/${subId}/datacenter_shared/proxy-list?type=getproxies&protocol=${proto}`, {
                                headers: { 'Authorization': `Bearer ${token}` }
                            });
                            if (pRes.ok) {
                                const pText = await pRes.text();
                                if (pText && pText.trim().length > 5 && !pText.includes('No protocol provided') && !pText.includes('error')) {
                                    return { ok: true, text: pText, subUser, subPass, subId };
                                }
                            }
                        } catch (e) {}

                        // 备选 1: displayproxies json 格式
                        try {
                            const jRes = await fetch(`/v2/v4/account/${subId}/datacenter_shared/proxy-list?format=json&type=displayproxies&protocol=${proto}`, {
                                headers: { 'Authorization': `Bearer ${token}` }
                            });
                            if (jRes.ok) {
                                const jData = await jRes.json();
                                if (Array.isArray(jData) && jData.length > 0) {
                                    return { ok: true, data: jData, subUser, subPass, subId };
                                }
                            }
                        } catch (e) {}

                        // 备选 2: format=txt
                        try {
                            const tRes = await fetch(`/v2/v4/account/${subId}/datacenter_shared/proxy-list?format=txt&protocol=${proto}`, {
                                headers: { 'Authorization': `Bearer ${token}` }
                            });
                            if (tRes.ok) {
                                const tText = await tRes.text();
                                if (tText && tText.trim().length > 5 && !tText.includes('No protocol provided') && !tText.includes('error')) {
                                    return { ok: true, text: tText, subUser, subPass, subId };
                                }
                            }
                        } catch (e) {}
                    }

                    return { error: 'api_failed', subId, me: meData, subUser, subPass };
                } catch (e) {
                    return { error: String(e) };
                }
            }""")
            logger.info(f"🔍 [ProxyScrape API] 内部接口返回结果: {str(api_res)[:300]}")
            if isinstance(api_res, dict) and api_res.get("ok"):
                raw_data = api_res.get("data") or api_res.get("text")
                sub_user = str(api_res.get("subUser") or "")
                sub_pass = str(api_res.get("subPass") or "")

                lines = []
                if isinstance(raw_data, list):
                    for item in raw_data:
                        if isinstance(item, str):
                            lines.append(item)
                        elif isinstance(item, dict):
                            ip = item.get("ip") or item.get("proxy_address") or item.get("host")
                            port = item.get("port")
                            user = item.get("username") or item.get("user") or sub_user
                            pwd = item.get("password") or item.get("pass") or sub_pass
                            if ip and port:
                                if user and pwd:
                                    lines.append(f"http://{user}:{pwd}@{ip}:{port}")
                                else:
                                    lines.append(f"http://{ip}:{port}")
                elif isinstance(raw_data, str):
                    matches = re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}(?::[^\s:]+:[^\s:]+)?", raw_data)
                    for m in matches:
                        parts = m.split(":")
                        if len(parts) == 2 and sub_user and sub_pass:
                            lines.append(f"http://{sub_user}:{sub_pass}@{parts[0]}:{parts[1]}")
                        else:
                            lines.append(m)

                if lines:
                    normalized = self._normalize_proxy_lines(lines)
                    return normalized, sub_user, sub_pass
        except Exception as ex:
            logger.debug(f"[ProxyScrape API] 提取异常: {ex}")
        return [], "", ""

    def _handle_cf(self, page: Page) -> None:
        try:
            class _Wrap:
                def __init__(self, p):
                    self.page = p
                    self._is_headless = self.page.context.browser is not None

                def execute_script(self, s, *a):
                    return self.page.evaluate(s, a)

            solve_or_wait_cloudflare_turnstile(_Wrap(page), timeout=15, is_headless=self.headless)
        except Exception:
            pass

    def _solve_turnstile_widget(self, page: Page) -> bool:
        """针对表单内嵌 Cloudflare Turnstile 验证框进行视口滚动与坐标/元素点击。"""
        try:
            # 1. 若已获取到有效 Token 或已通过验证，不再重复点击避免重置挑战
            has_token = page.evaluate("""() => {
                // 检索 React Fiber 状态
                try {
                    const root = document.querySelector('#__next') || document.body;
                    let rf = null;
                    for (const k in root) {
                        if (k.startsWith('__reactContainer$') || k.startsWith('__reactFiber$')) { rf = root[k]; break; }
                    }
                    const q = [rf];
                    while (q.length > 0) {
                        const n = q.shift();
                        if (!n) continue;
                        if (n.type && (n.type.name === 'F' || (typeof n.type === 'function' && String(n.type).includes('cf_turnstile_token')))) {
                            let h = n.memoizedState;
                            let idx = 0;
                            while (h) {
                                if (idx === 4 && typeof h.memoizedState === 'string' && h.memoizedState.length > 10) return true;
                                idx++;
                                h = h.next;
                            }
                            break;
                        }
                        if (n.child) q.push(n.child);
                        if (n.sibling) q.push(n.sibling);
                    }
                } catch (e) {}

                // 检索 Shadow DOM
                function findInShadow(r) {
                    if (!r) return false;
                    const el = r.querySelector && r.querySelector('[name="cf-turnstile-response"]');
                    if (el && el.value && el.value.length > 10) return true;
                    const all = r.querySelectorAll ? r.querySelectorAll('*') : [];
                    for (const c of all) {
                        if (c.shadowRoot && findInShadow(c.shadowRoot)) return true;
                    }
                    return false;
                }
                if (findInShadow(document.body)) return true;

                // 检索标准 DOM
                const inp = document.querySelector('[name="cf-turnstile-response"]');
                return !!(inp && inp.value && inp.value.length > 10);
            }""")
            if has_token:
                return True
        except Exception:
            pass

        try:
            # 1. 深度滚动所有滚动容器到底部，确保 Turnstile 完整进入视口
            page.evaluate("""() => {
                window.scrollTo(0, document.body.scrollHeight);
                document.querySelectorAll('*').forEach(el => {
                    if (el.scrollHeight > el.clientHeight + 20) {
                        el.scrollTop = el.scrollHeight;
                    }
                });
            }""")
            page.wait_for_timeout(300)

            # 2. 严格且仅对 Cloudflare 专属 iframe 检查状态，杜绝主页面 DOM 误判！
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                f_url = str(getattr(frame, "url", "") or "")
                if "cookiebot" in f_url:
                    continue

                try:
                    f_status = frame.evaluate("""() => {
                        let allText = '';
                        let hasSpinner = false;
                        let hasCb = false;

                        function scan(node) {
                            if (!node) return;
                            if (node.innerText) allText += ' ' + node.innerText.toLowerCase();
                            if (node.textContent) allText += ' ' + node.textContent.toLowerCase();
                            if (node.querySelector) {
                                if (node.querySelector('.spinner, #spinner, [role="progressbar"]')) hasSpinner = true;
                                const cb = node.querySelector('input[type="checkbox"], [role="checkbox"], .ctp-checkbox');
                                if (cb && !cb.checked) hasCb = true;
                            }
                            if (node.shadowRoot) scan(node.shadowRoot);
                            if (node.children) {
                                for (const c of node.children) scan(c);
                            }
                        }
                        scan(document.body);

                        const hasTextSpinner = ['verifying', 'vérification', 'verificando', 'wird überprüft', 'tarkistetaan', 'checking', 'bloqué', 'stuck', 'troubleshoot', 'jumissa'].some(w => allText.includes(w));
                        const isVerifying = !hasCb && (hasSpinner || hasTextSpinner);
                        const isSuccess = ['success'].some(w => allText.includes(w)) || !!document.querySelector('#success, .success');
                        const isFailed = ['échec', 'echec', 'failed', 'failure', 'fehlgeschlagen', 'fallida', 'falha', 'epäonnistui', 'сбой', '失败', '失敗'].some(w => allText.includes(w));
                        return { hasCb, isVerifying, isSuccess, isFailed };
                    }""", timeout=2000)
                    if f_status:
                        if f_status.get("isSuccess"):
                            return True
                        if f_status.get("isVerifying"):
                            logger.info("⏳ [Turnstile] 正在计算质询凭证 (Verifying...)，静默保持，避免重击打断...")
                            return False
                        if f_status.get("isFailed"):
                            logger.warning("⚠️ [Turnstile] 挑战状态为失败 (Failed)，跳过重复点击")
                            return False
                except Exception:
                    pass

            # 3. 定位真实渲染就绪的 Turnstile iframe 元素 (width > 100 且 height > 40)
            target_box = None
            target_cf_frame = None
            for frame in page.frames:
                f_url = str(getattr(frame, "url", "") or "")
                if any(k in f_url for k in ("challenges.cloudflare.com", "challenge-platform")):
                    try:
                        f_tok = frame.evaluate("() => document.querySelector('[name=\"cf-turnstile-response\"]')?.value", timeout=1500)
                        if f_tok and len(f_tok) > 10:
                            return True
                    except Exception:
                        pass

                    try:
                        el = frame.frame_element()
                        b = el.bounding_box() if el else None
                        if b and b.get("width", 0) > 100 and b.get("height", 0) > 40:
                            target_box = b
                            target_cf_frame = frame
                            break
                    except Exception:
                        pass

            if not target_box:
                for ifr in page.locator('iframe[src*="cloudflare"], iframe[src*="challenge"]').all():
                    try:
                        b = ifr.bounding_box()
                        if b and b.get("width", 0) > 100 and b.get("height", 0) > 40:
                            target_box = b
                            break
                    except Exception:
                        pass

            # 4. 若为 explicit/invisible 模式，主动调用 window.turnstile.execute() 触发计算
            page.evaluate("""() => {
                try {
                    if (window.turnstile && typeof window.turnstile.execute === 'function') {
                        const inp = document.querySelector('[name="cf-turnstile-response"]');
                        const wId = inp && inp.id ? inp.id.replace('_response', '') : undefined;
                        window.turnstile.execute(wId);
                    }
                } catch(e) {}
            }""")

            if target_box:
                # 严密计算：Turnstile 300x65 框内，复选框 [ ] 的水平中心精准位于 box['x'] + 28.5，垂直中心在 box['y'] + (box['height'] / 2.0)
                cx = target_box["x"] + 28.5 + random.uniform(-0.5, 0.5)
                cy = target_box["y"] + (target_box["height"] / 2.0) + random.uniform(-0.5, 0.5)

                # 仿生拟人移动与按下-释放周期
                page.mouse.move(cx - 30, cy - 20)
                page.wait_for_timeout(random.randint(60, 100))
                page.mouse.move(cx, cy)
                page.wait_for_timeout(random.randint(120, 180))
                page.mouse.down()
                page.wait_for_timeout(random.randint(70, 110))
                page.mouse.up()
                logger.info(f"🎯 [Turnstile] 精确复选框物理中心已拟人点击 (坐标: {cx:.1f}, {cy:.1f})")

                # 穿透保障：若跨域沙箱捕获到目标 frame，在内部同步派发一次原生 DOM 勾选事件
                if target_cf_frame:
                    try:
                        target_cf_frame.evaluate("""() => {
                            const cb = document.querySelector('input[type="checkbox"], [role="checkbox"], .ctp-checkbox');
                            if (cb && !cb.checked) {
                                cb.click();
                            }
                        }""")
                    except Exception:
                        pass

                page.wait_for_timeout(800)
                return True
        except Exception as ex:
            logger.debug(f"[TurnstileWidget] 辅助点击略过: {ex}")
        return False

    def _extract_proxies(self, page: Page) -> list[str]:
        # 从页面文本、textarea 或 pre 提取标准格式
        try:
            text_candidates = [page.inner_text("body")]
            for ta in page.locator("textarea, pre").all():
                try:
                    val = ta.input_value() if hasattr(ta, "input_value") else ""
                    text_candidates.append(val or ta.inner_text())
                except Exception:
                    pass

            combined = "\n".join(text_candidates)
            matches = re.findall(r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d{2,5}:[^\s:]+:[^\s:]+", combined)
            if matches:
                return self._normalize_proxy_lines(matches)
        except Exception:
            pass
        return []

    @staticmethod
    def _normalize_proxy_lines(raw_lines: list[str]) -> list[str]:
        res = []
        for line in raw_lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith("http://") or line.startswith("https://") or line.startswith("socks5://"):
                res.append(line)
                continue
            parts = line.split(":")
            if len(parts) == 4:
                ip, port, user, pwd = parts
                res.append(f"http://{user}:{pwd}@{ip}:{port}")
            elif "@" in line:
                res.append(f"http://{line}")
        return res
