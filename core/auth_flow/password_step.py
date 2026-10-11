# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import random
import string
import time
from typing import Callable

from core.humanize import delay as human_delay
from .human_actions import (
    _log_prefix, _human_click, _human_type_text, _find_any, _click_any,
    _type_any, _page_snapshot, _click_continue, _handle_blank_page,
)
from .stages import (
    _detect_current_auth_stage, _is_signup_password_page, _is_login_password_page,
    _is_email_verification_page, _check_already_verified, _check_manual_stop,
    _password_page_state,
)
from .session_step import _has_access_token

logger = logging.getLogger(__name__)


def _generate_roxy_password() -> str:
    """参考 FlowPilot 密码策略：8~64 位，含大小写、数字、符号。"""
    upper = 'ABCDEFGHJKLMNPQRSTUVWXYZ'
    lower = 'abcdefghjkmnpqrstuvwxyz'
    digits = '23456789'
    symbols = '!@#$%^&*?_-+=' 
    groups = [upper, lower, digits, symbols]
    all_chars = ''.join(groups)
    chars = [random.choice(g) for g in groups]
    while len(chars) < 14:
        chars.append(random.choice(all_chars))
    random.shuffle(chars)
    return ''.join(chars)


def _registration_password() -> str:
    try:
        from config import register as _register_cfg
        configured = str(getattr(_register_cfg, 'REGISTER_PASSWORD', '') or '').strip()
        if configured:
            return configured
    except Exception:
        pass
    return _generate_roxy_password()


def _click_passwordless_signup_if_present(driver) -> dict:
    """
    新版注册/登录流在 password 页可能默认要求密码。
    如果页面提供“使用一次性验证码”按钮，优先点击进入邮箱 OTP 页面。
    """
    try:
        result = driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
        const enabled = el => !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
        const norm = s => String(s || '').replace(/\s+/g, '').toLowerCase();
        const candidates = [...document.querySelectorAll('button,a,input[type="submit"],[role="button"],[role="link"]')].filter(el => visible(el) && enabled(el));
        const isPasswordlessOtp = el => {
          const name = String(el.getAttribute('name') || '').toLowerCase();
          const value = String(el.getAttribute('value') || '').toLowerCase();
          const attrs = [
            el.id, name, value, el.getAttribute('aria-label'), el.getAttribute('title'),
            el.getAttribute('data-testid'), el.getAttribute('data-dd-action-name'), el.className, el.textContent
          ].join(' ').toLowerCase();
          const text = norm(el.textContent || el.getAttribute('value') || '');
          return (
            (name === 'intent' && value.includes('passwordless') && value.includes('send_otp')) ||
            (name === 'intent' && value.includes('passwordless') && value.includes('otp')) ||
            (name === 'intent' && value === 'passwordless_signup_send_otp') ||
            (name === 'intent' && value === 'passwordless_login_send_otp') ||
            attrs.includes('passwordless_signup_send_otp') ||
            attrs.includes('passwordless_login_send_otp') ||
            /passwordless.*otp|otp.*passwordless|one[-_\s]?time.*code|code.*one[-_\s]?time/.test(attrs) ||
            text.includes('使用一次性验证码注册') ||
            text.includes('使用一次性验证码登录') ||
            text.includes('使用一次性验证码') ||
            text.includes('使用一次性驗證碼註冊') ||
            text.includes('使用一次性驗證碼登入') ||
            text.includes('一次性验证码') ||
            text.includes('一次性驗證碼') ||
            text.includes('メールでコード') ||
            text.includes('ワンタイムコード') ||
            text.includes('認証コード') ||
            text.includes('useonetimeregistrationcode') ||
            text.includes('useaone-timecodetosignup') ||
            text.includes('useaone-timecodetoregister') ||
            text.includes('useaone-timecodetologin') ||
            text.includes('continuewithaone-timecode') ||
            text.includes('loginwithaone-timecode') ||
            text.includes('signupwithaone-timecode') ||
            text.includes('one-timecode')
          );
        };
        const btn = candidates.find(isPasswordlessOtp);
        if (!btn) return {ok:false, reason:'missing_passwordless_button'};
        btn.scrollIntoView({block:'center'});
        return {
          ok:true,
          reason:'passwordless_send_otp_target',
          button: btn,
          name: btn.getAttribute('name') || '',
          value: btn.getAttribute('value') || '',
          text: (btn.textContent || '').trim().slice(0, 80)
        };
        """) or {"ok": False, "reason": "empty_result"}
        if result.get("ok") and result.get("button"):
            _human_click(driver, result.get("button"), label="passwordless_otp")
            result["reason"] = "clicked_passwordless_send_otp"
            result.pop("button", None)
        return result
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _click_continue_with_password_if_present(driver) -> dict:
    """在邮箱验证码页点击“使用密码继续”，进入 /create-account/password。"""
    try:
        result = driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
        const enabled = el => !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
        const norm = s => String(s || '').replace(/\s+/g, '').toLowerCase();
        const candidates = [...document.querySelectorAll('a,button,[role="link"],[role="button"]')].filter(el => visible(el) && enabled(el));
        const isPasswordCreate = el => {
          const href = String(el.getAttribute('href') || '').toLowerCase();
          const attrs = [
            href, el.id, el.getAttribute('aria-label'), el.getAttribute('title'),
            el.getAttribute('data-testid'), el.getAttribute('data-login-web-auth-control'),
            el.getAttribute('data-dd-action-name'), el.className, el.textContent
          ].join(' ').toLowerCase();
          const text = norm(el.textContent || '');
          return (
            href.includes('/create-account/password') ||
            attrs.includes('/create-account/password') ||
            attrs.includes("data-dd-action-name") ||
            text.includes('continuewithpassword') ||
            text.includes('continuewithapassword')
          );
        };
        const btn = candidates.find(isPasswordCreate);
        if (!btn) return {ok:false, reason:'missing_continue_with_password'};
        btn.scrollIntoView({block:'center'});
        return {
          ok:true,
          reason:'continue_with_password_target',
          button: btn,
          href: btn.getAttribute('href') || '',
          text: (btn.textContent || '').trim().slice(0, 80)
        };
        """) or {"ok": False, "reason": "empty_result"}
        if result.get("ok") and result.get("button"):
            _human_click(driver, result.get("button"), label="continue_with_password")
            result["reason"] = "clicked_continue_with_password"
            result.pop("button", None)
        return result
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _fill_password_page_if_present(driver, email: str, timeout: int = 25) -> str | None:
    """邮箱提交后兼容 create-account/password。返回本次设置的 OpenAI 账号密码；未遇到密码页返回 None。"""
    end = time.time() + timeout
    last = {}
    clicked_continue_password = False
    blank_start_time = None
    missing_pwd_input_start: float | None = None
    refreshed_count = 0
    max_refreshes = 2
    while time.time() < end:
        if _is_email_verification_page(driver):
            # 邮箱验证码页：OpenAI 现行默认链路已直接发送 OTP 邮件，无需主动点击“使用密码继续”分流，
            # 避免把正常的 OTP 流程强行打乱到密码创建页导致卡滞。直接返回 None 交由 OTP 阶段处理。
            logger.info("%s 当前处于邮箱验证码页，直接跳过设密分支交由 OTP 阶段处理", _log_prefix(driver))
            return None
        if _has_access_token(driver):
            return None
        last = _password_page_state(driver)
        blank_start_time = _handle_blank_page(driver, last, blank_start_time)
        is_signup_password = _is_signup_password_page(driver)
        is_login_password = _is_login_password_page(driver)
        if not (is_signup_password or is_login_password):
            time.sleep(0.5)
            continue
        passwordless = _click_passwordless_signup_if_present(driver) if is_login_password else {"ok": False, "reason": "signup_password_prefers_password"}
        if passwordless.get('ok'):
            logger.info("%s 检测到 password 页，已点击一次性验证码入口：email=%s detail=%s", _log_prefix(driver), email, passwordless)
            wait_end = time.time() + 20
            while time.time() < wait_end:
                if _is_email_verification_page(driver):
                    logger.info("%s 一次性验证码入口已进入邮箱验证码页", _log_prefix(driver))
                    return None
                if _has_access_token(driver):
                    logger.info("%s 一次性验证码入口后已检测到登录态", _log_prefix(driver))
                    return None
                time.sleep(0.5)
            logger.info("%s 已点击一次性验证码入口，未立即检测到 OTP 页，交给后续 OTP 阶段继续处理", _log_prefix(driver))
            return None
        if is_login_password:
            logger.info("%s 当前是登录密码页但未找到一次性验证码入口，跳过密码填写并交给 OTP 阶段：state=%s", _log_prefix(driver), last)
            return None
        password = _registration_password()
        logger.info("%s 检测到 create-account/password，准备设置密码（%s 位）：email=%s", _log_prefix(driver), len(password), email)
        result = driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
          && !el.disabled && !el.readOnly;
        const input = [...document.querySelectorAll('input[type="password"],input[name*="password" i],input[autocomplete="new-password"]')]
          .find(visible);
        if (!input) return {ok:false, reason:'missing_password_input'};
        const form = input.closest('form');
        const scope = form || document;
        const buttons = [...scope.querySelectorAll('button,input[type="submit"]')]
          .filter(el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length) && !el.disabled && el.getAttribute('aria-disabled') !== 'true')
          .map((el, idx) => {
            const r = el.getBoundingClientRect();
            const ir = input.getBoundingClientRect();
            return {el, idx, below: r.top >= ir.bottom - 10, dist: Math.max(0, r.top - ir.bottom) + Math.abs((r.left+r.right-ir.left-ir.right)/2)/10};
          })
          .filter(x => x.below)
          .sort((a,b) => a.dist - b.dist || a.idx - b.idx);
        if (!buttons.length) return {ok:false, reason:'missing_submit'};
        buttons[0].el.scrollIntoView({block:'center'});
        return {ok:true, reason:'password_targets', input, button: buttons[0].el};
        """) or {}
        if not result.get('ok'):
            now = time.time()
            if missing_pwd_input_start is None:
                missing_pwd_input_start = now
            elif (now - missing_pwd_input_start) >= 4.0 and refreshed_count < max_refreshes:
                refreshed_count += 1
                logger.warning(
                    "%s 处于密码创建页但超过 4.0 秒未挂载出输入框（白板/未渲染），主动执行第 %d/%d 次刷新自愈...",
                    _log_prefix(driver), refreshed_count, max_refreshes
                )
                try:
                    driver.refresh()
                    time.sleep(2.0)
                except Exception as ref_exc:
                    logger.warning("%s 密码页刷新自愈异常: %s", _log_prefix(driver), ref_exc)
                missing_pwd_input_start = None
                blank_start_time = None
                continue
            time.sleep(0.5)
            continue
        missing_pwd_input_start = None
        _human_type_text(driver, result.get("input"), password, clear=True)
        # React/Auth0 会在 input/change 后异步校验密码强度并启用 Continue。
        # 之前输入完 0.4~1.4s 就点，偶发点在按钮还未真正可提交/事件未绑定完成时，页面无反应。
        human_delay("form", minimum=2.0, maximum=3.6)
        submit_result = driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none';
        const enabled = el => !!el && !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
        const pass = [...document.querySelectorAll('input[type="password"],input[name*="password" i],input[autocomplete="new-password"]')]
          .find(el => visible(el) && !el.disabled && !el.readOnly);
        const form = pass ? pass.closest('form') : document.querySelector('form');
        const scope = form || document;
        const candidates = [...scope.querySelectorAll('button,input[type="submit"],[role="button"]')]
          .filter(el => visible(el) && enabled(el));
        const norm = s => String(s || '').replace(/\s+/g, '').toLowerCase();
        const scored = candidates.map((el, idx) => {
          const attrs = [
            el.getAttribute('type'), el.getAttribute('data-dd-action-name'),
            el.getAttribute('data-login-web-auth-control'), el.getAttribute('aria-label'),
            el.getAttribute('name'), el.getAttribute('value'), el.textContent
          ].join(' ').toLowerCase();
          const text = norm(el.textContent || el.getAttribute('value') || '');
          let score = 0;
          if ((el.getAttribute('type') || '').toLowerCase() === 'submit') score += 80;
          if ((el.getAttribute('data-dd-action-name') || '').toLowerCase() === 'continue') score += 90;
          if (String(el.getAttribute('data-login-web-auth-control') || '').toLowerCase() === 'true') score += 70;
          if (/continue|next|submit|create|続行/.test(attrs) || /continue|next|submit|create|続行/.test(text)) score += 50;
          return {el, idx, score};
        }).sort((a,b) => b.score - a.score || a.idx - b.idx);
        const target = scored[0]?.el;
        if (!target) return {ok:false, reason:'missing_enabled_submit'};
        target.scrollIntoView({block:'center'});
        return {
          ok:true,
          reason:'enabled_submit_target',
          button: target,
          text: (target.textContent || target.getAttribute('value') || '').trim().slice(0, 80),
          type: target.getAttribute('type') || '',
          dd: target.getAttribute('data-dd-action-name') || '',
          ariaDisabled: target.getAttribute('aria-disabled') || ''
        };
        """) or {}
        if not submit_result.get("ok") or not submit_result.get("button"):
            raise RuntimeError(f"密码页找不到可点击的 Continue 按钮：{submit_result} state={_password_page_state(driver)}")
        _human_click(driver, submit_result.get("button"), label="password_submit")
        logger.info("%s 已填写并点击密码页 Continue：detail=%s", _log_prefix(driver), {k: v for k, v in submit_result.items() if k != "button"})
        try:
            from selenium.webdriver.common.keys import Keys
            pwd_input = result.get("input")
            if pwd_input:
                pwd_input.send_keys(Keys.ENTER)
        except Exception:
            pass
        # 提交密码后通常进入邮箱验证码页，最多等一段时间。
        wait_end = time.time() + 20
        retried_submit = False
        while time.time() < wait_end:
            if _is_email_verification_page(driver):
                logger.info("%s 密码提交后已进入邮箱验证码页", _log_prefix(driver))
                return password
            if _has_access_token(driver):
                logger.info("%s 密码提交后已检测到登录态", _log_prefix(driver))
                return password
            if not retried_submit and time.time() > wait_end - 15 and _is_signup_password_page(driver):
                retried_submit = True
                logger.info("%s 密码页点击后仍未跳转，等待后重试一次 Continue/Enter", _log_prefix(driver))
                human_delay("form", minimum=1.2, maximum=2.2)
                try:
                    _click_continue(driver)
                except Exception:
                    try:
                        from selenium.webdriver.common.keys import Keys
                        driver.switch_to.active_element.send_keys(Keys.ENTER)
                    except Exception:
                        pass
            if not _is_signup_password_page(driver):
                return password
            time.sleep(0.5)
        if _is_signup_password_page(driver):
            logger.warning("%s 密码页等待超时且仍停留在密码设置页，判定为密码提交未生效", _log_prefix(driver))
            return None
        return password
    logger.info("%s 未检测到密码页，继续后续流程 last=%s", _log_prefix(driver), last)
    return None

