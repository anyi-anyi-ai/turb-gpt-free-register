# -*- coding: utf-8 -*-
"""Grok (accounts.x.ai) 注册页面交互流程。

迁移并优化自开源项目 grok-register，适配 Playwright/CloakBrowser 驱动。
"""
from __future__ import annotations

import logging
import random
import re
import string
import time
from typing import Any, Callable

logger = logging.getLogger(__name__)

SIGNUP_URL = "https://accounts.x.ai/sign-up?redirect=grok-com"

_ALREADY_REGISTERED_PATTERNS = (
    re.compile(r"existing account found", re.I),
    re.compile(r"email.{0,80}already.{0,40}(?:registered|exists|in use|used|taken)", re.I),
    re.compile(r"account.{0,80}already.{0,40}(?:registered|exists)", re.I),
    re.compile(r"already.{0,40}registered.{0,80}email", re.I),
    re.compile(r"user.{0,60}already.{0,40}exists", re.I),
    re.compile(r"email.{0,40}(?:is|address is).{0,20}(?:unavailable|not available)", re.I),
    re.compile(r"邮箱.{0,40}(?:已|已经).{0,40}(?:注册|存在|使用|占用)"),
    re.compile(r"账号.{0,40}(?:已|已经).{0,40}(?:注册|存在)"),
    re.compile(r"找到现有(?:账号|账户)"),
    re.compile(r"已存在与此邮箱地址关联的(?:账号|账户)"),
)

_DOMAIN_REJECTION_PATTERNS = (
    re.compile(r"(?:email|address).{0,30}(?:not available|invalid|unsupported|not allowed|blocked|disallowed)", re.I),
    re.compile(r"temporar(?:y|ily).{0,30}(?:unavailable|blocked)", re.I),
    re.compile(r"邮箱.{0,20}(?:不可用|不支持|被拒绝|格式无效|暂不支持)"),
    re.compile(r"无法使用此邮箱"),
)


class GrokSignupError(Exception):
    """Grok 注册通用错误"""
    pass


class GrokEmailDomainRejectedError(GrokSignupError):
    """邮箱域名被 x.ai 拒绝"""
    pass


class GrokAccountAlreadyExistsError(GrokSignupError):
    """该邮箱已注册过 Grok/x.ai"""
    pass


class GrokTurnstileTimeoutError(GrokSignupError):
    """Turnstile 验证码穿透超时"""
    pass


class GrokLoginError(GrokSignupError):
    """Grok 登录与恢复错误"""
    pass


class GrokAccountBannedError(GrokLoginError):
    """Grok 账号被封禁或停用"""
    pass


_BANNED_ACCOUNT_PATTERNS = (
    re.compile(r"account.{0,30}(?:suspended|disabled|deactivated|locked|blocked|banned)", re.I),
    re.compile(r"your account has been (?:suspended|disabled|deactivated|locked|blocked|banned)", re.I),
    re.compile(r"unable to sign in", re.I),
    re.compile(r"access denied", re.I),
    re.compile(r"this account has been deactivated", re.I),
    re.compile(r"账号.{0,20}(?:已封禁|被封禁|已停用|被冻结|异常|无法登录)"),
    re.compile(r"账户.{0,20}(?:已封禁|被封禁|已停用|被冻结|异常|无法登录)"),
)



class GrokSignupFlow:
    def __init__(self, page, log_callback: Callable[[str], None] | None = None):
        self.page = page
        self.log_callback = log_callback or (lambda msg: logger.info(f"[GrokFlow] {msg}"))

    def log(self, message: str) -> None:
        self.log_callback(f"[GrokFlow] {message}")

    def dismiss_cookie_banner(self) -> None:
        try:
            # 1. 优先使用 Playwright 原生 Locator 穿透点击 Cookie 弹窗
            loc = self.page.locator('button:has-text("Accept All Cookies"), button:has-text("Accept all"), button[aria-label="Dismiss cookie notice"]')
            if loc.count() > 0 and loc.first.is_visible():
                loc.first.click(timeout=1500)
                self.log("已通过原生 Locator 关闭 Cookie 提示横幅")
                return
        except Exception:
            pass

        try:
            self.page.evaluate(r"""() => {
                const oneTrustBtn = document.querySelector('#onetrust-accept-btn-handler, #accept-recommended-btn-handler, .banner-close-button');
                if (oneTrustBtn) { oneTrustBtn.click(); return; }
                const btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (const b of btns) {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (['accept all cookies', 'accept all', 'accept', 'agree', '同意', '全部接受', '接受'].some(k => t === k || t.includes(k))) {
                        b.click(); return;
                    }
                }
            }""")
        except Exception:
            pass

    def open_signup_page(self, timeout_ms: int = 45000) -> None:
        self.log(f"正在打开注册页面: {SIGNUP_URL}")
        try:
            if hasattr(self.page, "context") and hasattr(self.page.context, "clear_cookies"):
                self.page.context.clear_cookies()
        except Exception:
            pass
        try:
            self.page.goto(SIGNUP_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            exc_str = str(exc)
            self.log(f"页面跳转异常: {exc_str}")
            if any(k in exc_str for k in ("ERR_TUNNEL_CONNECTION_FAILED", "ERR_CONNECTION_REFUSED", "ERR_PROXY_CONNECTION_FAILED")):
                raise GrokSignupError(f"代理网络无法建立隧道连接: {exc_str}") from exc
            time.sleep(2)
        time.sleep(2)
        self.dismiss_cookie_banner()

    def click_email_signup_button(self, timeout_sec: int = 15) -> bool:
        """点击「使用邮箱注册 / Sign up with email」"""
        self.log("正在定位「使用邮箱注册」按钮...")
        deadline = time.time() + timeout_sec
        keywords = [
            "sign up with email", "signup with email", "使用邮箱注册",
            "continue with email", "regístrate con correo", "registrate con correo",
            "s'inscrire avec", "mit e-mail registrieren", "inscrever-se com email", "registrati con email"
        ]

        while time.time() < deadline:
            self.dismiss_cookie_banner()
            # 1. 优先尝试使用 Playwright 原生 Locator 触发真实 isTrusted 事件
            try:
                for kw in keywords:
                    loc = self.page.locator(f'button:has-text("{kw}"), a:has-text("{kw}"), [role="button"]:has-text("{kw}")')
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.click(timeout=2000)
                        self.log(f"已通过原生 Locator 点击「使用邮箱注册」: {kw}")
                        for _ in range(6):
                            time.sleep(0.5)
                            if self.page.locator('input[type="email"], input[name="email"], input').count() > 0:
                                return True
            except Exception:
                pass

            # 2. 回退到增强 JS 派发真实 Pointer/Mouse 事件并点击
            clicked = self.page.evaluate(r"""() => {
                function isVisible(node) {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                const keywords = [
                    "sign up with email", "signup with email", "使用邮箱注册",
                    "continue with email", "regístrate con correo", "registrate con correo",
                    "s'inscrire avec", "mit e-mail registrieren", "inscrever-se com email", "registrati con email"
                ];
                const nodes = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="button"]'));
                for (const n of nodes) {
                    if (!isVisible(n)) continue;
                    const text = (n.innerText || n.textContent || n.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (keywords.some(k => text.includes(k))) {
                        n.focus();
                        n.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                        n.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                        n.click();
                        return text;
                    }
                }
                return null;
            }""")
            if clicked:
                self.log(f"已点击「使用邮箱注册」按钮: {clicked}")
                for _ in range(6):
                    time.sleep(0.5)
                    if self.page.locator('input[type="email"], input[name="email"], input').count() > 0:
                        return True
            time.sleep(0.8)
        self.log("未找到邮箱注册按钮，可能已直接处于邮箱输入页")
        return False

    def fill_email_and_submit(self, email: str, timeout_sec: int = 30) -> None:
        """输入邮箱并提交"""
        self.log(f"正在输入邮箱: {email}")
        deadline = time.time() + timeout_sec
        filled = False

        while time.time() < deadline:
            self.dismiss_cookie_banner()
            try:
                self.handle_turnstile(timeout_sec=2)
            except Exception:
                pass

            # 1. 优先尝试 Playwright 原生 locator.fill (最天然适配 React 18 受控组件)
            try:
                for sel in ['input[type="email"]', 'input[name="email"]', 'input[autocomplete*="email"]', 'input[placeholder*="email" i]']:
                    loc = self.page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.fill(email, timeout=1500)
                        filled = True
                        break
            except Exception:
                pass
            if filled:
                break

            # 如果未找到输入框，检测是否还在首屏并尝试再次点击「使用邮箱注册」
            reclicked = self.page.evaluate(r"""() => {
                const keywords = ["sign up with email", "signup with email", "使用邮箱注册"];
                const nodes = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (const n of nodes) {
                    const t = (n.innerText || n.textContent || '').trim().toLowerCase();
                    if (keywords.some(k => t.includes(k))) {
                        n.click();
                        return t;
                    }
                }
                return null;
            }""")
            if reclicked:
                self.log(f"检测到仍处于首屏，再次点击「使用邮箱注册」: {reclicked}")
                time.sleep(1.5)

            # 2. 回退到 React 18 原型链 setter
            filled = self.page.evaluate(r"""(email) => {
                function isVisible(node) {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                function setInputValue(input, val) {
                    if (!input) return;
                    input.focus();
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                    const tracker = input._valueTracker;
                    if (tracker) tracker.setValue('');
                    if (nativeSetter) nativeSetter.call(input, val);
                    else input.value = val;
                    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: val, inputType: 'insertText' }));
                    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: val, inputType: 'insertText' }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }
                const inputs = Array.from(document.querySelectorAll('input[type="email"], input[name="email"], input[autocomplete*="email"], input'));
                for (const inp of inputs) {
                    if (!isVisible(inp) || inp.disabled) continue;
                    const typ = (inp.getAttribute('type') || '').toLowerCase();
                    const name = (inp.getAttribute('name') || '').toLowerCase();
                    const placeholder = (inp.getAttribute('placeholder') || '').toLowerCase();
                    const aria = (inp.getAttribute('aria-label') || '').toLowerCase();
                    if (typ === 'email' || name.includes('email') || placeholder.includes('email') || placeholder.includes('邮箱') || aria.includes('email') || aria.includes('邮箱')) {
                        setInputValue(inp, email);
                        return true;
                    }
                }
                return false;
            }""", email)
            if filled:
                break
            time.sleep(0.5)

        if not filled:
            try:
                curr_url = self.page.url
                curr_title = self.page.title()
                body_snip = self.page.evaluate("() => document.body ? document.body.innerText.slice(0, 300) : ''")
                self.log(f"未能定位邮箱输入框: url={curr_url}, title={curr_title}, text={body_snip}")
            except Exception:
                pass
            raise GrokSignupError("未能在页面中定位到邮箱输入框")

        time.sleep(0.5)
        self.log("正在点击继续/提交邮箱...")
        submitted = False
        try:
            for btn_text in ["Sign up", "Sign Up", "Confirm", "继续", "提交", "下一步", "Continue", "Next", "Submit", "Proceed"]:
                loc = self.page.locator(f'button:has-text("{btn_text}"), input[type="submit"]:has-text("{btn_text}")')
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=2000)
                    submitted = True
                    self.log(f"已通过原生 Locator 点击提交: {btn_text}")
                    break
        except Exception:
            pass

        if not submitted:
            submitted_js = self.page.evaluate(r"""() => {
                function isVisible(node) {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                const btns = Array.from(document.querySelectorAll('button[type="submit"], button, input[type="submit"]'));
                for (const b of btns) {
                    if (!isVisible(b) || b.disabled) continue;
                    const t = (b.innerText || b.textContent || b.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (['sign up', 'confirm', '继续', '提交', '下一步', 'continue', 'next', 'submit', 'proceed'].some(k => t.includes(k))) {
                        b.focus();
                        b.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                        b.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                        b.click();
                        return t;
                    }
                }
                return null;
            }""")
            if submitted_js:
                submitted = True
                self.log(f"已触发 JS 提交操作: {submitted_js}")

        if not submitted:
            self.log("尝试键盘 Enter 提交")
            self.page.keyboard.press("Enter")

        time.sleep(2)
        self.check_email_errors(email)

    def check_email_errors(self, email: str) -> None:
        """检查邮箱输入后的错误提示（已注册 / 域名受限）"""
        page_text = ""
        try:
            page_text = self.page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return

        for p in _ALREADY_REGISTERED_PATTERNS:
            if p.search(page_text):
                raise GrokAccountAlreadyExistsError(f"该邮箱已被注册: {email}")

        for p in _DOMAIN_REJECTION_PATTERNS:
            if p.search(page_text):
                raise GrokEmailDomainRejectedError(f"邮箱域名被 x.ai 拒收/不支持: {email}")

    def wait_for_otp_page(self, timeout_sec: int = 30, email: str = "") -> bool:
        """等待进入验证码输入界面"""
        self.log("正在等待验证码输入界面...")
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            if email:
                self.check_email_errors(email)
            has_otp = self.page.evaluate(r"""() => {
                function isVisible(node) {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                const inputs = Array.from(document.querySelectorAll('input'));
                const hasInput = inputs.some(inp => {
                    if (!isVisible(inp)) return false;
                    const name = (inp.getAttribute('name') || '').toLowerCase();
                    const testid = (inp.getAttribute('data-testid') || '').toLowerCase();
                    const auto = (inp.getAttribute('autocomplete') || '').toLowerCase();
                    const inputmode = (inp.getAttribute('inputmode') || '').toLowerCase();
                    return name.includes('code') || testid.includes('code') || auto === 'one-time-code' || inputmode === 'numeric';
                });
                if (hasInput) return true;
                const text = (document.body ? document.body.innerText : '').toLowerCase();
                return text.includes('verify your email') || text.includes('security code') || text.includes('confirm email');
            }""")
            if has_otp:
                self.log("已检测到验证码输入界面")
                return True
            time.sleep(1)
        return False

    def fill_code_and_submit(self, code: str, email: str = "", timeout_sec: int = 20) -> None:
        """回填 6 位验证码并提交"""
        clean_code = str(code).strip()
        self.log(f"正在回填验证码: {clean_code}")
        deadline = time.time() + timeout_sec
        filled = False

        while time.time() < deadline:
            filled_type = self.page.evaluate(r"""(code) => {
                function isVisible(node) {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                function setInputValue(input, val) {
                    if (!input) return;
                    input.focus();
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                    const tracker = input._valueTracker;
                    if (tracker) tracker.setValue('');
                    if (nativeSetter) nativeSetter.call(input, val);
                    else input.value = val;
                    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: val, inputType: 'insertText' }));
                    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: val, inputType: 'insertText' }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }

                const inputs = Array.from(document.querySelectorAll('input')).filter(isVisible);
                // 1. 单个完整验证码框
                const single = inputs.find(inp => {
                    const name = (inp.getAttribute('name') || '').toLowerCase();
                    const maxlen = parseInt(inp.getAttribute('maxlength') || '0', 10);
                    return (name.includes('code') || inp.getAttribute('autocomplete') === 'one-time-code') && maxlen !== 1;
                });
                if (single) {
                    setInputValue(single, code);
                    return 'single';
                }
                // 2. 分开的 6 个单字符验证码格子
                const boxes = inputs.filter(inp => {
                    const maxlen = parseInt(inp.getAttribute('maxlength') || '0', 10);
                    const mode = (inp.getAttribute('inputmode') || '').toLowerCase();
                    return maxlen === 1 || mode === 'numeric';
                });
                if (boxes.length >= code.length) {
                    for (let i = 0; i < code.length; i++) {
                        setInputValue(boxes[i], code[i]);
                    }
                    return 'boxes';
                }
                return null;
            }""", clean_code)
            if filled_type:
                self.log(f"验证码填写成功（模式: {filled_type}）")
                filled = True
                break
            time.sleep(0.8)

        if not filled:
            raise GrokSignupError("未找到匹配的验证码输入框")

        time.sleep(1)
        # 点击继续或确认 (优先原生 Locator)
        submitted = False
        try:
            for btn_text in ["Confirm email", "Confirm", "确认", "继续", "下一步", "验证", "Continue", "Next", "Verify", "Submit", "Sign up"]:
                loc = self.page.locator(f'button:has-text("{btn_text}"), button[type="submit"]:has-text("{btn_text}")')
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=2000)
                    submitted = True
                    self.log(f"通过原生 Locator 提交验证码: {btn_text}")
                    break
        except Exception:
            pass

        if not submitted:
            try:
                submit_btn = self.page.locator('button[type="submit"]')
                if submit_btn.count() > 0 and submit_btn.first.is_visible():
                    submit_btn.first.click(timeout=2000)
                    submitted = True
                    self.log("通过原生 Locator 点击 button[type='submit']")
            except Exception:
                pass

        if not submitted:
            submitted_js = self.page.evaluate(r"""() => {
                const btns = Array.from(document.querySelectorAll('button[type="submit"], button'));
                for (const b of btns) {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (['confirm email', 'confirm', '确认', '继续', '下一步', '验证', 'continue', 'next', 'verify', 'sign up'].some(k => t.includes(k))) {
                        b.focus();
                        b.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                        b.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                        b.click(); return t;
                    }
                }
                const firstSubmit = document.querySelector('button[type="submit"]');
                if (firstSubmit) {
                    firstSubmit.click();
                    return 'first_submit';
                }
                return null;
            }""")
            if submitted_js:
                submitted = True
                self.log(f"通过 JS 提交验证码: {submitted_js}")

        # 键盘 Enter 兜底提交
        try:
            self.page.keyboard.press("Enter")
            self.log("已触发键盘 Enter 兜底提交验证码")
        except Exception:
            pass
        time.sleep(2.5)
        if email:
            self.check_email_errors(email)

    def handle_turnstile(self, timeout_sec: int = 30) -> bool:
        """检测并穿透 Cloudflare Turnstile 验证码"""
        self.log("检查页面是否存在 Cloudflare Turnstile 挑战...")
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            has_cf = self.page.evaluate(r"""() => {
                const iframes = Array.from(document.querySelectorAll('iframe'));
                return iframes.some(f => {
                    const src = (f.getAttribute('src') || '').toLowerCase();
                    return src.includes('challenges.cloudflare.com') || src.includes('turnstile');
                });
            }""")
            if not has_cf:
                return True

            self.log("[Turnstile] 检测到 Cloudflare 挑战，尝试智能等待或穿透...")
            # 1. 尝试遍历 frames 点击 checkbox
            clicked_frame = False
            for frame in self.page.frames:
                if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                    try:
                        box = frame.locator('input[type="checkbox"], #challenge-stage, .ctp-checkbox-label, div[id*="cf-stage"]')
                        if box.count() > 0 and box.first.is_visible(timeout=1500):
                            box.first.click(timeout=1500)
                            self.log("[Turnstile] 已通过原生 Locator 点击 Turnstile 复选框")
                            clicked_frame = True
                            break
                    except Exception:
                        pass

            if not clicked_frame:
                for frame in self.page.frames:
                    if "challenges.cloudflare.com" in frame.url or "turnstile" in frame.url:
                        try:
                            body_info = frame.evaluate("""() => {
                                const b = document.body;
                                if (!b) return null;
                                const r = b.getBoundingClientRect();
                                return { w: r.width, h: r.height };
                            }""")
                            if body_info and body_info.get("h", 0) > 0:
                                click_x = 24
                                click_y = body_info["h"] / 2
                                frame.click("body", position={"x": click_x, "y": click_y}, timeout=2000)
                                self.log(f"[Turnstile] 已物理坐标点击 Turnstile frame body ({click_x}, {click_y:.0f})")
                                clicked_frame = True
                                break
                        except Exception:
                            pass

            if not clicked_frame:
                try:
                    cf_loc = self.page.locator('iframe[src*="challenges.cloudflare.com"]')
                    if cf_loc.count() > 0 and cf_loc.first.is_visible():
                        cf_loc.first.click(timeout=1500)
                except Exception:
                    pass

            time.sleep(2)
            # 再次检查是否已生成响应 Token
            solved = self.page.evaluate(r"""() => {
                const tokenInput = document.querySelector('input[name="cf-turnstile-response"]');
                return tokenInput && tokenInput.value && tokenInput.value.length >= 80;
            }""")
            if solved:
                self.log("[Turnstile] 挑战已自动解决 (检测到有效的 Response Token >= 80)")
                return True

        return True

    def click_turnstile_frame(self) -> bool:
        """定位 Turnstile frame 并执行精准坐标点击 (x=24, y=h/2)。"""
        frames = getattr(self.page, "frames", None)
        if not frames:
            return False
        for frame in frames:
            frame_url = str(frame.url or "").lower()
            if "challenges.cloudflare.com" in frame_url or "turnstile" in frame_url:
                try:
                    box = frame.locator('input[type="checkbox"], #challenge-stage, .ctp-checkbox-label, div[id*="cf-stage"]')
                    if box.count() > 0 and box.first.is_visible(timeout=1000):
                        box.first.click(timeout=1000)
                        self.log("[Turnstile] 已通过原生 Locator 点击复选框")
                        return True
                except Exception:
                    pass

                try:
                    body_info = frame.evaluate("""() => {
                        const b = document.body;
                        if (!b) return null;
                        const r = b.getBoundingClientRect();
                        return { w: r.width, h: r.height };
                    }""")
                    if body_info and body_info.get("h", 0) > 0:
                        click_x = 24
                        click_y = body_info["h"] / 2
                        frame.click("body", position={"x": click_x, "y": click_y}, timeout=2000)
                        self.log(f"[Turnstile] 已物理坐标点击 Turnstile frame body ({click_x}, {click_y:.0f})")
                        return True
                except Exception:
                    pass
        return False

    def wait_turnstile_solved(self, timeout_sec: int = 35) -> bool:
        """提交前守卫：等待 Cloudflare Turnstile token 就绪 (>= 80)，若未通过则主动坐标点击穿透。"""
        deadline = time.time() + timeout_sec
        click_attempted = False
        last_click = 0.0

        while time.time() < deadline:
            cf_info = self.page.evaluate(r"""() => {
                const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
                const hasIframe = !!document.querySelector('iframe[src*="turnstile"], iframe[src*="challenges.cloudflare.com"]');
                let token = String((cfInput && cfInput.value) || '').trim();
                if (!token && window.turnstile && typeof window.turnstile.getResponse === 'function') {
                    try { token = String(window.turnstile.getResponse() || '').trim(); } catch(e) {}
                }
                return { has_cf: !!cfInput || hasIframe, token_len: token.length, token: token };
            }""")
            if not cf_info or not cf_info.get("has_cf"):
                return True

            token_len = cf_info.get("token_len", 0)
            if token_len >= 80:
                self.log(f"[Turnstile] 提交前守卫验证通过: Token 长度={token_len}")
                token_val = cf_info.get("token", "")
                if token_val:
                    self.page.evaluate(r"""(token) => {
                        const cfInput = document.querySelector('input[name="cf-turnstile-response"]');
                        if (cfInput) {
                            cfInput.value = token;
                            cfInput.dispatchEvent(new Event('input', { bubbles: true }));
                            cfInput.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""", token_val)
                return True

            now = time.time()
            # 首次或每隔 4 秒尝试点击一次 Turnstile frame
            if not click_attempted or (now - last_click >= 4.0):
                self.log(f"[Turnstile] 人机挑战未就绪 (当前token长度={token_len})，尝试主动点击 Turnstile...")
                self.click_turnstile_frame()
                click_attempted = True
                last_click = now
                time.sleep(2.0)
                continue

            time.sleep(1.0)

        self.log("[Turnstile] 提交前守卫超时，未获取到完整 Token，将尝试继续表单操作")
        return False

    def fill_profile_and_submit(
        self,
        password: str,
        given_name: str = "",
        family_name: str = "",
        timeout_sec: int = 40,
        email: str = "",
    ) -> None:
        """填写个人姓名与密码并提交注册"""
        if not given_name:
            given_names = ["Alex", "James", "Ethan", "David", "Daniel", "Lucas", "Noah", "Leo", "Emma", "Olivia"]
            given_name = random.choice(given_names)
        if not family_name:
            family_names = ["Miller", "Smith", "Johnson", "Brown", "Taylor", "Wilson", "Davis", "Clark"]
            family_name = random.choice(family_names)

        self.log(f"正在填写个人资料: 姓名={given_name} {family_name}...")
        deadline = time.time() + timeout_sec
        filled = False

        while time.time() < deadline:
            if email:
                self.check_email_errors(email)
            has_turnstile = self.page.evaluate(r"""() => {
                const iframes = Array.from(document.querySelectorAll('iframe'));
                return iframes.some(f => {
                    const src = (f.getAttribute('src') || '').toLowerCase();
                    return src.includes('challenges.cloudflare.com') || src.includes('turnstile');
                });
            }""")
            if has_turnstile:
                self.handle_turnstile(timeout_sec=3)

            filled = self.page.evaluate(r"""([given, family, pwd]) => {
                function isVisible(node) {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                const inputs = Array.from(document.querySelectorAll('input')).filter(isVisible);
                const givenInput = inputs.find(i => {
                    const n = (i.getAttribute('name') || '').toLowerCase();
                    const testid = (i.getAttribute('data-testid') || '').toLowerCase();
                    const auto = (i.getAttribute('autocomplete') || '').toLowerCase();
                    return n.includes('given') || n.includes('first') || testid.includes('given') || testid.includes('first') || auto.includes('given');
                });
                const familyInput = inputs.find(i => {
                    const n = (i.getAttribute('name') || '').toLowerCase();
                    const testid = (i.getAttribute('data-testid') || '').toLowerCase();
                    const auto = (i.getAttribute('autocomplete') || '').toLowerCase();
                    return n.includes('family') || n.includes('last') || testid.includes('family') || testid.includes('last') || auto.includes('family');
                });
                const nameInput = inputs.find(i => {
                    const n = (i.getAttribute('name') || '').toLowerCase();
                    const testid = (i.getAttribute('data-testid') || '').toLowerCase();
                    const auto = (i.getAttribute('autocomplete') || '').toLowerCase();
                    const p = (i.getAttribute('placeholder') || '').toLowerCase();
                    return (n.includes('name') || testid.includes('name') || auto.includes('name') || p.includes('name')) && !n.includes('user') && !n.includes('given') && !n.includes('family');
                });
                const pwdInput = inputs.find(i => {
                    const typ = (i.getAttribute('type') || '').toLowerCase();
                    const n = (i.getAttribute('name') || '').toLowerCase();
                    return typ === 'password' || n.includes('password');
                });

                function setInputValue(input, val) {
                    if (!input) return;
                    input.focus();
                    const nativeSetter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')?.set;
                    const tracker = input._valueTracker;
                    if (tracker) tracker.setValue('');
                    if (nativeSetter) nativeSetter.call(input, val);
                    else input.value = val;
                    input.dispatchEvent(new InputEvent('beforeinput', { bubbles: true, data: val, inputType: 'insertText' }));
                    input.dispatchEvent(new InputEvent('input', { bubbles: true, data: val, inputType: 'insertText' }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }

                if (givenInput && familyInput && pwdInput) {
                    setInputValue(givenInput, given);
                    setInputValue(familyInput, family);
                    setInputValue(pwdInput, pwd);
                    return true;
                } else if (nameInput && pwdInput) {
                    setInputValue(nameInput, given + ' ' + family);
                    setInputValue(pwdInput, pwd);
                    return true;
                } else if (pwdInput && !givenInput && !familyInput) {
                    setInputValue(pwdInput, pwd);
                    return true;
                }
                return false;
            }""", [given_name, family_name, password])
            if filled:
                break
            time.sleep(1.0)

        if not filled:
            if email:
                self.check_email_errors(email)
            cur_url = self.page.url
            body_text = ""
            try:
                body_text = self.page.evaluate("() => document.body ? document.body.innerText : ''")
            except Exception:
                pass
            self.log(f"表单定位失败，URL={cur_url}，页面内容摘要: {body_text[:250]}")
            raise GrokSignupError(f"未能在页面中定位姓名与密码表单 (当前页面: {cur_url})")

        # 提交前验证 Cloudflare Turnstile Token 守卫（主动点击穿透）
        self.wait_turnstile_solved(timeout_sec=35)

        time.sleep(0.8)
        self.log("正在提交最终注册表单...")
        submitted = False
        try:
            for btn_text in ["注册", "创建账号", "继续", "Sign up", "Create account", "Continue", "Submit"]:
                loc = self.page.locator(f'button:has-text("{btn_text}"), button[type="submit"]:has-text("{btn_text}")')
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=2000)
                    submitted = True
                    self.log(f"通过原生 Locator 提交个人资料: {btn_text}")
                    break
        except Exception:
            pass

        if not submitted:
            self.page.evaluate(r"""() => {
                const btns = Array.from(document.querySelectorAll('button[type="submit"], button, input[type="submit"]'));
                for (const b of btns) {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (['注册', '创建账号', '继续', 'sign up', 'create account', 'continue', 'submit'].some(k => t.includes(k))) {
                        b.focus();
                        b.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                        b.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                        b.click(); return;
                    }
                }
            }""")
        time.sleep(2)

    def wait_for_sso_cookie(self, timeout_sec: int = 65) -> tuple[str, str]:
        """等待注册完成并提取 sso 与 sso-rw Cookie。"""
        self.log("正在等待 Grok 登录态生效并提取 SSO Cookie...")
        deadline = time.time() + timeout_sec
        started = time.time()
        sso_token = ""
        sso_rw = ""
        nudged_grok = False
        last_nudge_at = 0.0

        def read_cookies():
            nonlocal sso_token, sso_rw
            try:
                cookies = self.page.context.cookies()
                for c in cookies:
                    name = str(c.get("name", "")).strip()
                    val = str(c.get("value", "")).strip()
                    if name == "sso" and val and not sso_token:
                        sso_token = val
                    elif name == "sso-rw" and val and not sso_rw:
                        sso_rw = val
            except Exception:
                pass
            return sso_token or sso_rw

        while time.time() < deadline:
            now = time.time()
            elapsed = now - started

            if read_cookies():
                self.log(f"成功提取到 SSO Cookie! (sso={bool(sso_token)}, sso-rw={bool(sso_rw)}, 前缀={sso_token[:15] if sso_token else sso_rw[:15]}...)")
                return (sso_token or sso_rw), sso_rw

            # 1. 如果页面有「前往 Grok / 继续」类按钮，推动自然重定向
            try:
                for btn_text in ["继续", "前往", "打开", "开始使用", "Continue", "Proceed", "Go to", "grok.com"]:
                    loc = self.page.locator(f'button:has-text("{btn_text}"), a:has-text("{btn_text}")')
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.click(timeout=1500)
                        self.log(f"已点击继续类按钮推动登录跳转: {btn_text}")
                        time.sleep(1)
                        break
            except Exception:
                pass

            if read_cookies():
                return (sso_token or sso_rw), sso_rw

            # 2. 如果在 accounts.x.ai 上停留超过 10 秒仍无 sso，执行一次轻量 nudge 跳转 grok.com 触发 Set-Cookie 写入
            if elapsed >= 10 and not nudged_grok and (now - last_nudge_at >= 10):
                self.log("accounts.x.ai 等待超过 10s，轻量打开 grok.com 触发会话生效...")
                try:
                    self.page.goto("https://grok.com/", wait_until="domcontentloaded", timeout=20000)
                except Exception as e:
                    self.log(f"跳转 grok.com 提示: {e}")
                nudged_grok = True
                last_nudge_at = now
                time.sleep(2)

            time.sleep(1.5)

        if not sso_token and sso_rw:
            sso_token = sso_rw
            return sso_token, sso_rw

        raise GrokSignupError("超时未获取到有效的 Grok SSO Cookie")

    def authorize_device_in_browser(
        self,
        user_code: str,
        open_url: str,
        timeout_sec: int = 60,
    ) -> bool:
        """在已登录的指纹浏览器会话中打开 Device 授权页，完成「继续」→「允许」授权。

        对齐 CPA (CLIProxyAPI) / x.ai 官方 OAuth2 Device Flow 规范。
        """
        user_code = str(user_code or "").strip()
        open_url = str(open_url or "").strip()
        if not user_code or not open_url:
            self.log("[DeviceAuth] 授权参数不完整")
            return False

        self.log(f"[DeviceAuth] 正在打开 Device 授权页 (user_code={user_code}): {open_url[:100]}...")

        # 导航到 open_url
        try:
            self.page.goto(open_url, wait_until="domcontentloaded", timeout=40000)
        except Exception as exc:
            self.log(f"[DeviceAuth] 页面跳转提示: {exc}")

        time.sleep(1.5)
        self.dismiss_cookie_banner()

        deadline = time.time() + timeout_sec
        clicked_continue = False
        clicked_allow = False

        while time.time() < deadline:
            # 1. 检查是否需要先点击「继续 / Continue」
            if not clicked_continue:
                try:
                    for cont_text in ["继续", "下一步", "确认", "Continue", "Next", "Confirm"]:
                        loc = self.page.locator(f'button:has-text("{cont_text}"), [role="button"]:has-text("{cont_text}")')
                        if loc.count() > 0 and loc.first.is_visible():
                            loc.first.click(timeout=2000)
                            clicked_continue = True
                            self.log(f"[DeviceAuth] 已通过原生 Locator 点击「继续」按钮: {cont_text}")
                            time.sleep(1.5)
                            break
                except Exception:
                    pass

            # 2. 检查是否出现「允许 / Allow / Approve」授权按钮
            try:
                for allow_text in ["允许", "同意", "Allow", "Approve", "Accept"]:
                    loc = self.page.locator(f'button:has-text("{allow_text}"), [role="button"]:has-text("{allow_text}")')
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.click(timeout=2000)
                        clicked_allow = True
                        self.log(f"[DeviceAuth] 已通过原生 Locator 点击「允许」授权按钮: {allow_text}")
                        time.sleep(2)
                        return True
            except Exception:
                pass

            # 3. 如果原生定位器未命中，使用增强 JS 派发真实 Pointer/Mouse 事件
            js_clicked = self.page.evaluate(r"""() => {
                function isVisible(node) {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                const allowKeys = ["允许", "allow", "approve", "同意", "accept"];
                const continueKeys = ["继续", "continue", "下一步", "next"];
                const denyKeys = ["拒绝", "deny", "cancel", "取消"];
                const btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));

                // 优先允许
                for (const b of btns) {
                    if (!isVisible(b) || b.disabled) continue;
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (denyKeys.some(k => t.includes(k))) continue;
                    if (allowKeys.some(k => t.includes(k))) {
                        b.focus();
                        b.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                        b.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                        b.click();
                        return 'allow:' + t;
                    }
                }
                // 其次继续
                for (const b of btns) {
                    if (!isVisible(b) || b.disabled) continue;
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (denyKeys.some(k => t.includes(k))) continue;
                    if (continueKeys.some(k => t.includes(k))) {
                        b.focus();
                        b.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                        b.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                        b.click();
                        return 'continue:' + t;
                    }
                }
                return null;
            }""")
            if js_clicked:
                self.log(f"[DeviceAuth] JS 触发按钮操作: {js_clicked}")
                if "allow" in js_clicked:
                    clicked_allow = True
                    time.sleep(2)
                    return True
                elif "continue" in js_clicked:
                    clicked_continue = True
                    time.sleep(1.5)

            time.sleep(1)

        self.log("[DeviceAuth] 设备授权页面交互结束")
        return clicked_continue or clicked_allow


SIGNIN_URL = "https://accounts.x.ai/sign-in?redirect=grok-com"


class GrokSignInFlow(GrokSignupFlow):
    """Grok 账号登录与凭据恢复流（基于 Email sign-in code 免密验证码）。"""

    def open_signin_page(self, timeout_ms: int = 45000) -> None:
        self.log(f"正在打开登录页面: {SIGNIN_URL}")
        try:
            if hasattr(self.page, "context") and hasattr(self.page.context, "clear_cookies"):
                self.page.context.clear_cookies()
        except Exception:
            pass
        try:
            self.page.goto(SIGNIN_URL, wait_until="domcontentloaded", timeout=timeout_ms)
        except Exception as exc:
            exc_str = str(exc)
            self.log(f"页面跳转异常: {exc_str}")
            if any(k in exc_str for k in ("ERR_TUNNEL_CONNECTION_FAILED", "ERR_CONNECTION_REFUSED", "ERR_PROXY_CONNECTION_FAILED")):
                raise GrokLoginError(f"代理网络无法建立隧道连接: {exc_str}") from exc
            time.sleep(2)
        time.sleep(2)
        self.dismiss_cookie_banner()

    def click_email_signin_button(self, timeout_sec: int = 15) -> bool:
        """点击「使用邮箱登录 / Login with email」"""
        self.log("正在定位「使用邮箱登录」按钮...")
        deadline = time.time() + timeout_sec
        keywords = [
            "login with email", "sign in with email", "使用邮箱登录", "邮箱登录",
            "continue with email", "inicia sesión con correo", "iniciar sesión con correo",
            "se connecter avec", "mit e-mail anmelden", "acessar com email", "accedi con email"
        ]

        while time.time() < deadline:
            self.dismiss_cookie_banner()
            # 1. 优先尝试使用 Playwright 原生 Locator
            try:
                for kw in keywords:
                    loc = self.page.locator(f'button:has-text("{kw}"), a:has-text("{kw}"), [role="button"]:has-text("{kw}")')
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.click(timeout=2000)
                        self.log(f"已通过原生 Locator 点击「使用邮箱登录」: {kw}")
                        for _ in range(6):
                            time.sleep(0.5)
                            if self.page.locator('input[type="email"], input[name="email"], input').count() > 0:
                                return True
            except Exception:
                pass

            # 2. 回退到 JS
            clicked = self.page.evaluate(r"""() => {
                function isVisible(node) {
                    if (!node) return false;
                    const style = window.getComputedStyle(node);
                    if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                }
                const keywords = [
                    "login with email", "sign in with email", "使用邮箱登录", "邮箱登录",
                    "continue with email"
                ];
                const nodes = Array.from(document.querySelectorAll('button, a, [role="button"], input[type="button"]'));
                for (const n of nodes) {
                    if (!isVisible(n)) continue;
                    const text = (n.innerText || n.textContent || n.getAttribute('aria-label') || '').trim().toLowerCase();
                    if (keywords.some(k => text.includes(k))) {
                        n.focus();
                        n.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                        n.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                        n.click();
                        return text;
                    }
                }
                return null;
            }""")
            if clicked:
                self.log(f"已通过 JS 点击「使用邮箱登录」按钮: {clicked}")
                for _ in range(6):
                    time.sleep(0.5)
                    if self.page.locator('input[type="email"], input[name="email"], input').count() > 0:
                        return True
            time.sleep(0.8)
        self.log("未找到邮箱登录按钮，可能已直接处于邮箱输入页")
        return False

    def check_login_page_errors(self) -> None:
        """检查登录页面是否提示封号、禁止登录或风控错误"""
        body_text = ""
        try:
            body_text = self.page.evaluate("() => document.body ? document.body.innerText : ''")
        except Exception:
            return

        for p in _BANNED_ACCOUNT_PATTERNS:
            if p.search(body_text):
                self.log(f"[BANNED] 检测到账号已被封禁/限制登录: {p.pattern}")
                raise GrokAccountBannedError(f"账号被封禁或停用: {p.pattern}")

    def fill_email_and_next(self, email: str, timeout_sec: int = 25) -> None:
        """填写邮箱并点击 Next"""
        self.log(f"正在输入邮箱: {email}")
        deadline = time.time() + timeout_sec
        filled = False

        while time.time() < deadline:
            self.dismiss_cookie_banner()
            try:
                for sel in ['input[type="email"]', 'input[name="email"]', 'input[autocomplete*="email"]', 'input[placeholder*="email" i]']:
                    loc = self.page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        loc.first.fill(email, timeout=1500)
                        filled = True
                        break
            except Exception:
                pass
            if filled:
                break
            time.sleep(0.8)

        if not filled:
            # 回退 JS 填写
            filled_js = self.page.evaluate(r"""(email) => {
                const inputs = Array.from(document.querySelectorAll('input'));
                for (const inp of inputs) {
                    const t = (inp.getAttribute('type') || '').toLowerCase();
                    const n = (inp.getAttribute('name') || '').toLowerCase();
                    if (t === 'email' || n.includes('email') || t === 'text') {
                        inp.focus();
                        inp.value = email;
                        inp.dispatchEvent(new Event('input', { bubbles: true }));
                        inp.dispatchEvent(new Event('change', { bubbles: true }));
                        return true;
                    }
                }
                return false;
            }""", email)
            if not filled_js:
                raise GrokLoginError(f"无法在登录页面定位邮箱输入框")

        time.sleep(0.8)
        self.log("正在点击「Next」进入验证方式选择...")
        # 点击 Next 按钮
        clicked_next = False
        try:
            for next_text in ["Next", "下一步", "Continue", "继续"]:
                loc = self.page.locator(f'button:has-text("{next_text}"), button[type="submit"]:has-text("{next_text}")')
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=1500)
                    clicked_next = True
                    break
        except Exception:
            pass

        if not clicked_next:
            try:
                self.page.keyboard.press("Enter")
            except Exception:
                pass

        time.sleep(3)
        self.check_login_page_errors()

    def request_email_signin_code(self, timeout_sec: int = 20) -> float:
        """点击「Email sign-in code」触发 x.ai 发送验证码，返回请求时间戳"""
        self.log("正在寻找并点击「Email sign-in code」...")
        deadline = time.time() + timeout_sec

        while time.time() < deadline:
            self.check_login_page_errors()
            try:
                for text_kw in ["Email sign-in code", "Email code", "邮箱验证码登录", "发送验证码", "Send code"]:
                    loc = self.page.locator(f'button:has-text("{text_kw}"), [role="button"]:has-text("{text_kw}")')
                    if loc.count() > 0 and loc.first.is_visible():
                        send_ts = time.time() - 2.0
                        loc.first.click(timeout=2000)
                        self.log(f"已点击「{text_kw}」按钮")
                        time.sleep(2)
                        return send_ts
            except Exception:
                pass

            # 回退 JS 查找
            clicked = self.page.evaluate(r"""() => {
                const btns = Array.from(document.querySelectorAll('button, a, [role="button"]'));
                for (const b of btns) {
                    const t = (b.innerText || b.textContent || '').trim().toLowerCase();
                    if (t.includes('sign-in code') || t.includes('email code') || t.includes('验证码')) {
                        b.click();
                        return t;
                    }
                }
                return null;
            }""")
            if clicked:
                send_ts = time.time() - 2.0
                self.log(f"通过 JS 点击了「{clicked}」")
                time.sleep(2)
                return send_ts

            time.sleep(1)

        raise GrokLoginError("未能在页面找到「Email sign-in code」选项")

    def submit_signin_code(self, code: str, timeout_sec: int = 30) -> None:
        """填写登录验证码，穿透 Turnstile 并提交"""
        self.log(f"正在填写登录验证码: {code}")
        code_str = str(code).strip()
        filled = False
        try:
            inp = self.page.locator('input[name="code"], input[type="text"], input[autocomplete="one-time-code"]')
            if inp.count() > 0 and inp.first.is_visible():
                inp.first.fill(code_str, timeout=2000)
                filled = True
        except Exception:
            pass

        if not filled:
            # 尝试多输入框模式
            self.fill_code_and_submit(code=code_str)
            return

        time.sleep(1)
        # 等待 Turnstile 穿透守卫
        self.wait_turnstile_solved(timeout_sec=35)

        # 点击提交
        time.sleep(0.8)
        self.log("正在提交登录表单...")
        submitted = False
        try:
            for btn_kw in ["Log in", "Sign in", "登录", "Continue", "继续", "Next", "下一步", "Verify", "验证"]:
                loc = self.page.locator(f'button:has-text("{btn_kw}"), button[type="submit"]:has-text("{btn_kw}")')
                if loc.count() > 0 and loc.first.is_visible():
                    loc.first.click(timeout=2000)
                    submitted = True
                    self.log(f"已通过原生 Locator 点击登录提交: {btn_kw}")
                    break
        except Exception:
            pass

        if not submitted:
            try:
                self.page.keyboard.press("Enter")
                self.log("已按 Enter 提交登录表单")
            except Exception:
                pass

        time.sleep(3)
        self.check_login_page_errors()

