# -*- coding: utf-8 -*-
"""
Stable Audio (stableaudio.com) 自动化注册核心服务模块 (基于 BaseRegistrationFlow 模板方法重构)
架构层级：
  - 驱动层：基于 CloakBrowser 指纹浏览器 + 出口代理规避风控
  - 授权层：自动化打通 Auth0 Universal Login 注册表单（无需邮件 OTP 直接建号）
  - 准入层：自动勾选条款并同意服务协议（Terms of Service），直接激活 50 点免费音频额度
  - 凭据解耦层：从 SPA LocalStorage 提取 Access Token、Refresh Token 与 Cookies，落盘并推送网关
"""
from __future__ import annotations

import json
import logging
import os
import secrets
import string
import time
from pathlib import Path
from typing import Any, Dict, Optional

from core.cloakbrowser_driver import build_cloak_driver
from core.flows.base import BaseRegistrationFlow
from core.flows.context import FlowContext, RegistrationResult
from core.flows.pool_helper import pick_unregistered_email_for_platform
from core.stableaudio2api_sync import (
    DEFAULT_DB_PATH,
    init_stableaudio_db,
    save_stableaudio_account_to_db,
    push_account_to_music2api,
)

logger = logging.getLogger("stableaudio.register")
DB_PATH = DEFAULT_DB_PATH


def _generate_secure_password(length: int = 16) -> str:
    """生成符合 Auth0 高强度的安全密码 (大小写字母 + 数字 + 特殊符号, CSPRNG)。"""
    upper = secrets.choice(string.ascii_uppercase)
    lower = secrets.choice(string.ascii_lowercase)
    digit = secrets.choice(string.digits)
    special = secrets.choice("!@#$%&*")
    remain = [secrets.choice(string.ascii_letters + string.digits + "!@#$%&*") for _ in range(length - 4)]
    chars = [upper, lower, digit, special] + remain
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


class StableAudioRegistrationFlow(BaseRegistrationFlow):
    """Stable Audio 注册流水线实现。"""

    platform_name = "stableaudio"
    platform_table = "stableaudio_accounts"

    def build_driver(self, context: FlowContext) -> Any:
        driver, _ = build_cloak_driver(
            proxy=context.proxy,
            headless=context.headless,
            account_email=context.email,
        )
        return driver

    def execute(self, context: FlowContext) -> RegistrationResult:
        driver = context.driver
        email = context.email
        # Auth0 对密码强度有强制性校验（长度>=8，包含大写、小写、数字、特殊符号），
        # 邮箱池自带的邮箱密码可能较弱，此处统一生成符合 Auth0 标准的高强独立服务密码。
        password = _generate_secure_password()
        context.password = password
        p = Path(context.db_path or DB_PATH)

        logger.info(f"[*] 开始注册 Stable Audio 账号: {email}")

        try:
            # 1. 访问首页
            logger.info("[1/5] 打开 Stable Audio 官网...")
            driver.get("https://stableaudio.com/")
            time.sleep(5)

            # 清除/接受 Cookie 弹窗，防止遮挡导航栏
            driver.page.evaluate('''() => {
                const cb = document.querySelector('#onetrust-accept-btn-handler') || 
                           Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').includes('Accept All Cookies'));
                if (cb) cb.click();
                const banner = document.querySelector('#onetrust-consent-sdk');
                if (banner) banner.remove();
            }''')
            time.sleep(1)

            # 2. 点击 Sign up (带重试直至跳转 Auth0)
            logger.info("[2/5] 点击 'Sign up' 按钮跳转 Auth0...")
            auth0_loaded = False
            for attempt in range(8):
                driver.page.evaluate('''() => {
                    const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Sign up');
                    if (btn) btn.click();
                }''')
                time.sleep(2)
                if "auth0.com" in driver.page.url:
                    auth0_loaded = True
                    break

            if not auth0_loaded:
                raise RuntimeError(f"未能跳转至 Auth0 认证页面，当前 URL: {driver.page.url}")

            time.sleep(2)

            # 3. 填充并提交注册表单（拟人化输入与原生事件触发，规避 Auth0 机械风控拦截）
            logger.info("[3/5] 填充 Auth0 注册表单并提交...")
            email_input = driver.page.locator("input#email, input[name='email']").first
            email_input.wait_for(state="visible", timeout=15000)
            try:
                email_input.click(force=True)
            except Exception:
                email_input.focus()
            time.sleep(0.3)
            # 使用 Playwright 原生逐键输入以触发受控组件真实的按键时序
            email_el_dom = driver.page.locator("input#email, input[name='email']").first
            email_el_dom.press_sequentially(email, delay=35)
            time.sleep(0.5)

            pass_input = driver.page.locator("input#password, input[name='password']").first
            pass_input.wait_for(state="visible", timeout=15000)
            try:
                pass_input.click(force=True)
            except Exception:
                pass_input.focus()
            time.sleep(0.3)
            pass_input.press_sequentially(password, delay=40)
            time.sleep(0.8)

            # 模拟鼠标悬停与点击提交
            submit_btn = driver.page.locator("button[type='submit'][name='action'], button[type='submit']").first
            submit_btn.wait_for(state="visible", timeout=10000)
            submit_btn.hover()
            time.sleep(0.3)
            submit_btn.click()

            # 4. 等待授权回调返回 stableaudio.com
            logger.info("[4/5] 等待 OAuth 授权回调完成...")
            returned_to_sa = False
            for i in range(45):
                time.sleep(1)
                try:
                    page_state = driver.page.evaluate('''() => {
                        const text = (document.body && document.body.innerText) || '';
                        const href = window.location.href;
                        const errEl = document.querySelector('.error, [role="alert"], #error-cs, .ulp-input-error-message, span[id*="error"]');
                        const errText = errEl ? (errEl.innerText || '').trim() : '';
                        const onSA = href.includes('stableaudio.com') && !href.includes('auth0.com');
                        const hasTerms = text.includes('Terms of Service') || text.includes('Free');
                        return { onSA: onSA || hasTerms, href, hasError: Boolean(errText), errorText: errText };
                    }''')
                    if page_state.get("hasError"):
                        raise RuntimeError(f"Auth0 拒绝注册: {page_state.get('errorText')}")
                    if page_state.get("onSA"):
                        logger.info(f"[*] OAuth 授权回调完成，跳转主站成功 (耗时 {i+1}s): {page_state.get('href')}")
                        returned_to_sa = True
                        break
                except RuntimeError:
                    raise
                except Exception:
                    pass
                if (i + 1) % 5 == 0:
                    logger.info(f"[*] 仍在等待 OAuth 回调 (已等待 {i+1}s)...")

            if not returned_to_sa:
                err_text = driver.page.evaluate('''() => {
                    const el = document.querySelector('.error, [role="alert"], #error-cs, .ulp-input-error-message, span[id*="error"]');
                    return el ? el.innerText : document.body.innerText.substring(0, 500);
                }''')
                raise RuntimeError(f"注册后未能成功重定向回主站，当前 URL: {driver.page.url}, 页面提示: {err_text}")

            time.sleep(4)

            # 5. 处理服务条款勾选与 Agree
            logger.info("[5/5] 确认服务条款并激活额度...")
            driver.page.evaluate('''() => {
                const cookieBtn = document.querySelector('#onetrust-accept-btn-handler') ||
                                  Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim().includes('Accept All Cookies'));
                if (cookieBtn) cookieBtn.click();
                const banner = document.querySelector('#onetrust-consent-sdk');
                if (banner) banner.remove();
                
                const cbs = Array.from(document.querySelectorAll('input[type="checkbox"]'));
                if (cbs.length > 0) {
                    cbs[0].click();
                    cbs[0].checked = true;
                    cbs[0].dispatchEvent(new Event('change', { bubbles: true }));
                }
            }''')
            time.sleep(1.5)

            driver.page.evaluate('''() => {
                const agreeBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Agree');
                if (agreeBtn) agreeBtn.click();
            }''')
            time.sleep(4)

            # 6. 提取 LocalStorage 中的 Auth0 凭证
            auth_data = driver.page.evaluate('''() => {
                const ls = {};
                for (let i = 0; i < localStorage.length; i++) {
                    const k = localStorage.key(i);
                    ls[k] = localStorage.getItem(k);
                }
                return ls;
            }''')

            cookies_list = driver.page.context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}

            access_token = ""
            refresh_token = ""
            token_type = "Bearer"

            for k, v in auth_data.items():
                if "auth0" in k.lower() and "openid" in k:
                    try:
                        parsed = json.loads(v)
                        body = parsed.get("body", {})
                        access_token = body.get("access_token", "")
                        refresh_token = body.get("refresh_token", "")
                        token_type = body.get("token_type", "Bearer")
                        break
                    except Exception:
                        pass

            return RegistrationResult(
                ok=True,
                email=email,
                platform=self.platform_name,
                access_token=access_token,
                credits=50,
                cookies=cookies_dict,
                status="active",
                metadata={
                    "password": password,
                    "refresh_token": refresh_token,
                    "token_type": token_type,
                },
            )

        except Exception as exc:
            try:
                save_stableaudio_account_to_db(
                    email=email,
                    password=password,
                    access_token="",
                    status="failed",
                    credits=0,
                    payload={"error": str(exc)},
                    db_path=p,
                )
            except Exception:
                pass
            raise

    def persist(self, context: FlowContext, result: RegistrationResult) -> bool:
        password = result.metadata.get("password") or context.password or ""
        refresh_token = result.metadata.get("refresh_token", "")
        token_type = result.metadata.get("token_type", "Bearer")
        p = Path(context.db_path or DB_PATH)
        return save_stableaudio_account_to_db(
            email=result.email,
            password=password,
            access_token=result.access_token,
            refresh_token=refresh_token,
            token_type=token_type,
            credits=result.credits,
            cookies=result.cookies,
            proxy_used=context.proxy or "",
            status="active" if result.ok else "failed",
            payload=context.extra_payload,
            db_path=p,
        )

    def push(self, context: FlowContext, result: RegistrationResult) -> bool:
        refresh_token = result.metadata.get("refresh_token", "")
        p = Path(context.db_path or DB_PATH)
        synced = push_account_to_music2api(
            email=result.email,
            access_token=result.access_token,
            refresh_token=refresh_token,
            cookies=result.cookies,
            credits=result.credits,
            db_path=p,
        )
        result.synced_to_gateway = synced
        return synced


# ============================================================
# 向后兼容公开 API (供 CLI、WebUI 与现有外部脚本调用)
# ============================================================

def pick_available_stableaudio_account(
    specific_email: Optional[str] = None,
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """从本地 SQLite 邮箱池中分配未在 stableaudio_accounts 注册过的可用邮箱。"""
    p = Path(db_path or DB_PATH)
    init_stableaudio_db(p)
    return pick_unregistered_email_for_platform(
        platform_table="stableaudio_accounts",
        specific_email=specific_email,
        db_path=p,
    )


def register_single_stableaudio_account(
    account_info: Dict[str, Any],
    proxy: str = "http://Americas:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260",
    headless: bool = True,
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """全自动完成单只 Stable Audio 账号注册、条款准入与凭据提取。"""
    p = Path(db_path or DB_PATH)
    init_stableaudio_db(p)

    flow = StableAudioRegistrationFlow(db_path=p)
    res = flow.run(
        account_info=account_info,
        proxy=proxy,
        headless=headless,
    )

    if res.ok:
        return {
            "ok": True,
            "email": res.email,
            "password": res.metadata.get("password", ""),
            "access_token": res.access_token,
            "refresh_token": res.metadata.get("refresh_token", ""),
            "credits": res.credits,
            "synced": res.synced_to_gateway,
        }
    else:
        return {
            "ok": False,
            "email": res.email,
            "error": res.error,
        }
