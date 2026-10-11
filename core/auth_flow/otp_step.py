# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import random
import time
from typing import Callable

from core.humanize import delay as human_delay
from .human_actions import (
    _log_prefix, _human_click, _human_type_text, _find_any, _click_any,
    _page_snapshot, _handle_blank_page, _visible, _browser_actions_enabled,
    _human_scroll_to,
)
from .stages import (
    _detect_current_auth_stage, _is_signup_password_page, _is_login_password_page,
    _is_email_verification_page, _check_already_verified, _check_manual_stop,
    _email_otp_page_state,
)

logger = logging.getLogger(__name__)


def _type_otp(driver, code: str, timeout: int = 25) -> None:
    try:
        from selenium.webdriver.common.by import By
    except ImportError:
        class By:  # type: ignore
            CSS_SELECTOR = "css selector"

    code = str(code or "").strip()
    if not code:
        raise RuntimeError("待输入的 OTP 为空")

    end = time.time() + max(timeout, 25)
    last_err = None
    blank_start_time = None
    missing_inputs_start = None
    refreshed_count = 0
    max_refreshes = 2

    while time.time() < end:
        try:
            # 1. 单输入框检测（覆盖 one-time-code / name / id / aria-label / placeholder 等）
            for selector in [
                "input[autocomplete='one-time-code']",
                "input[name='code']",
                "input[name='otp']",
                "input[id*='code' i]",
                "input[id*='otp' i]",
                "input[placeholder*='code' i]",
                "input[placeholder*='验证码']",
                "input[placeholder*='驗證碼']",
                "input[placeholder*='コード']",
                "input[aria-label*='code' i]",
                "input[aria-label*='digit' i]",
                "input[aria-label*='验证码']",
                "input[aria-label*='驗證碼']",
                "input[aria-label*='コード']",
                "input[inputmode='numeric']",
                "input[type='tel']",
                "input[maxlength='6']",
            ]:
                els = [e for e in driver.find_elements(By.CSS_SELECTOR, selector) if _visible(e)]
                if len(els) == 1:
                    target = els[0]
                    typed_ok = False
                    try:
                        _human_type_text(driver, target, code, clear=True)
                        val = str(target.get_attribute("value") or "").strip()
                        if val == code:
                            typed_ok = True
                    except Exception as type_exc:
                        last_err = type_exc

                    if not typed_ok:
                        driver.execute_script(r"""
                        const el = arguments[0];
                        const val = arguments[1];
                        el.scrollIntoView({block: 'center'});
                        el.focus();
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(el, val); else el.value = val;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        """, target, code)
                    try:
                        from selenium.webdriver.common.keys import Keys
                        target.send_keys(Keys.ENTER)
                    except Exception:
                        pass
                    return
                elif len(els) > 1:
                    single_candidates = [
                        e for e in els
                        if (e.get_attribute("maxlength") or "99") not in ("1", "2")
                    ]
                    if len(single_candidates) == 1:
                        target = single_candidates[0]
                        _human_type_text(driver, target, code, clear=True)
                        try:
                            from selenium.webdriver.common.keys import Keys
                            target.send_keys(Keys.ENTER)
                        except Exception:
                            pass
                        return

            # 2. 6 个分格输入框
            boxes = [e for e in driver.find_elements(By.CSS_SELECTOR, "input") if _visible(e)]
            numeric_boxes = []
            for e in boxes:
                attrs = " ".join(str(e.get_attribute(k) or "") for k in ("inputmode", "autocomplete", "aria-label", "name", "id", "type", "data-index"))
                if any(x in attrs.lower() for x in ("numeric", "one-time", "code", "otp", "tel", "digit")):
                    numeric_boxes.append(e)
            if len(numeric_boxes) >= len(code):
                for e, ch in zip(numeric_boxes, code):
                    if _browser_actions_enabled():
                        _human_scroll_to(driver, e)
                        time.sleep(random.uniform(0.03, 0.12))
                    try:
                        e.send_keys(ch)
                    except Exception:
                        driver.execute_script(r"""
                        const el = arguments[0];
                        const ch = arguments[1];
                        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
                        if (setter) setter.call(el, ch); else el.value = ch;
                        el.dispatchEvent(new Event('input', {bubbles: true}));
                        el.dispatchEvent(new Event('change', {bubbles: true}));
                        """, e, ch)
                    if _browser_actions_enabled():
                        human_delay("keystroke")
                try:
                    from selenium.webdriver.common.keys import Keys
                    numeric_boxes[-1].send_keys(Keys.ENTER)
                except Exception:
                    pass
                return

            # 3. DOM 直接查找兜底（解决 Selenium _visible 判定偏差）
            js_found = driver.execute_script(r"""
            const code = arguments[0];
            const inputs = [...document.querySelectorAll('input')].filter(el => {
              const r = el.getBoundingClientRect();
              return (r.width > 0 || r.height > 0) && !el.disabled && el.type !== 'hidden';
            });
            const otpInput = inputs.find(el => {
              const attrs = [el.name, el.id, el.autocomplete, el.placeholder, el.getAttribute('aria-label'), el.type, el.inputMode].join(' ').toLowerCase();
              return /one-time|otp|\bcode\b|numeric|digit/.test(attrs);
            });
            if (otpInput && (otpInput.maxLength === -1 || otpInput.maxLength >= 6)) {
              otpInput.scrollIntoView({block: 'center'});
              otpInput.focus();
              const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
              if (setter) setter.call(otpInput, code); else otpInput.value = code;
              otpInput.dispatchEvent(new Event('input', {bubbles: true}));
              otpInput.dispatchEvent(new Event('change', {bubbles: true}));
              return true;
            }
            return false;
            """, code)
            if js_found:
                try:
                    from selenium.webdriver.common.keys import Keys
                    driver.switch_to.active_element.send_keys(Keys.ENTER)
                except Exception:
                    pass
                return

        except Exception as exc:
            last_err = exc

        # 4. 白屏检测自愈守卫
        state = _email_otp_page_state(driver)
        blank_start_time = _handle_blank_page(driver, state, blank_start_time)

        # 5. 缺框白板定向刷新自愈：已处于验证码路由，但页面持续无输入框（如 React 挂起/白屏未完全渲染）
        inputs = state.get("inputs") or []
        curr_url = str(getattr(driver, "current_url", "") or state.get("url") or "").lower()
        if ("email-verification" in curr_url or "email_otp" in curr_url or _is_email_verification_page(driver)) and not inputs:
            now = time.time()
            if missing_inputs_start is None:
                missing_inputs_start = now
            elif (now - missing_inputs_start) >= 4.0 and refreshed_count < max_refreshes:
                refreshed_count += 1
                logger.warning(
                    "%s 处于验证码页面但超过 4.0 秒未检测到输入框（白板/渲染未完成），主动执行第 %d/%d 次刷新自愈...",
                    _log_prefix(driver), refreshed_count, max_refreshes
                )
                try:
                    driver.refresh()
                    time.sleep(2.0)
                except Exception as ref_exc:
                    logger.warning("%s 验证码页刷新自愈异常: %s", _log_prefix(driver), ref_exc)
                missing_inputs_start = None
                blank_start_time = None
                continue
        else:
            missing_inputs_start = None

        time.sleep(0.5)

    state = _email_otp_page_state(driver)
    current_url = getattr(driver, "current_url", "") or state.get("url") or ""
    page_text = (state.get("text") or "")[:200].replace("\n", " ")
    inputs_info = [
        f"{i.get('type')}:{i.get('name')}:{i.get('id')}:{i.get('autocomplete')}"
        for i in (state.get("inputs") or [])
    ]
    raise RuntimeError(
        f"找不到 OTP 输入框 (url={current_url}, inputs={inputs_info}, text={page_text}, err={last_err})"
    )


def _clear_otp_inputs(driver) -> None:
    try:
        driver.execute_script(r"""
        const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
        const inputs = [...document.querySelectorAll('input')].filter(visible).filter(el => {
          const attrs = [el.type, el.name, el.id, el.autocomplete, el.inputMode, el.getAttribute('aria-label')].join(' ').toLowerCase();
          return /one-time|otp|code|numeric|tel/.test(attrs);
        });
        for (const el of inputs) {
          const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
          if (setter) setter.call(el, ''); else el.value = '';
          el.dispatchEvent(new Event('input', {bubbles:true}));
          el.dispatchEvent(new Event('change', {bubbles:true}));
        }
        """)
    except Exception:
        pass


def _click_resend_email_otp(driver, timeout: int = 20) -> dict:
    """点击重新发送邮箱验证码。优先按 DOM 属性识别，文本仅兜底。"""
    end = time.time() + timeout
    last = None
    while time.time() < end:
        if not _is_email_verification_page(driver):
            logger.info("%s[OTP] 尝试重发验证码前发现页面已跳转，取消重发直接返回成功", _log_prefix(driver))
            return {"ok": True, "text": "already_left"}
        try:
            btn = driver.execute_script(r"""
            const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
            const enabled = el => !el.disabled && String(el.getAttribute('aria-disabled') || '').toLowerCase() !== 'true';
            const candidates = [...document.querySelectorAll('button,a,[role=button],[role=link],input[type=button],input[type=submit]')].filter(visible);
            const attrHit = candidates.find(el => {
              if (!enabled(el)) return false;
              const attrs = [el.id, el.getAttribute('name'), el.getAttribute('value'), el.getAttribute('data-dd-action-name'), el.getAttribute('aria-label'), el.getAttribute('title'), el.getAttribute('data-testid')]
                .join(' ').toLowerCase();
              const name = String(el.getAttribute('name') || '').toLowerCase();
              const value = String(el.getAttribute('value') || '').toLowerCase();
              if (name === 'intent' && value === 'resend') return true;
              return /resend|send.*new|new.*code|again/.test(attrs);
            });
            if (attrHit) return attrHit;
            // 兜底：多语言文本，避免因页面没有稳定属性时卡死。
            return candidates.find(el => enabled(el) && /resend|send\s+(?:a\s+)?new\s+code|send\s+again|重新发送|重新发送电子邮件|重发|再次发送|再送信|新しい|届かない/.test((el.innerText || el.textContent || '').toLowerCase())) || null;
            """)
            if btn:
                text = str(btn.text or btn.get_attribute('value') or btn.get_attribute('data-dd-action-name') or '').strip()
                _human_click(driver, btn, label="resend_otp")
                logger.info("%s[OTP] 已点击重新发送验证码按钮：%s", _log_prefix(driver), text or '-')
                time.sleep(random.uniform(1.1, 2.4) if _browser_actions_enabled() else 1.5)
                return {"ok": True, "text": text}
        except Exception as exc:
            last = exc
        time.sleep(0.5)
    raise RuntimeError(f"找不到可点击的重新发送验证码按钮: last={last}, state={_email_otp_page_state(driver)}")


def _wait_after_email_otp_submit(driver, timeout: int = 25) -> str:
    """提交 OTP 后等待页面离开验证码页。"""
    end = time.time() + timeout
    last = {}
    last_resubmit_attempt = time.time()
    while time.time() < end:
        time.sleep(0.5)
        # 1. 确认已跳转离开验证码路由（如已到 about-you、profile 或登录态）
        if not _is_email_verification_page(driver):
            return 'accepted'
        last = _email_otp_page_state(driver)
        invalid = any(str(i.get('ariaInvalid') or '').lower() == 'true' for i in (last.get('inputs') or []))
        if invalid or (last.get('errors') or []):
            logger.warning("%s[OTP] 检测到明确验证码错误标记: errors=%s", _log_prefix(driver), last.get('errors'))
            return 'invalid'
            
        page_text = (last.get("text") or "").lower()
        if "route error" in page_text or "500 internal" in page_text or "hoppla" in page_text:
            logger.warning("%s[OTP] 检测到 OpenAI Route Error (500)，尝试点击 Try again 按钮", _log_prefix(driver))
            try:
                driver.execute_script(r"""
                const btn = [...document.querySelectorAll('button, a')].find(b => 
                    /try again|erneut versuchen|重试/i.test(b.innerText || b.textContent || '')
                );
                if (btn) btn.click();
                """)
            except Exception:
                pass
            time.sleep(1.5)
            continue
        # 2. 关键自愈：如果提交后超过 3.5 秒仍停留在验证码页且输入框内有值，补点一次 Continue 与回车兜底
        now = time.time()
        if now - last_resubmit_attempt >= 3.5:
            last_resubmit_attempt = now
            inputs = last.get("inputs") or []
            has_code_val = any(len(str(i.get("value") or "").strip()) >= 5 for i in inputs)
            if has_code_val:
                logger.info("%s[OTP] 提交后仍在验证码页，主动执行补点 Continue 与回车兜底...", _log_prefix(driver))
                try:
                    _click_continue(driver)
                except Exception:
                    pass
                try:
                    from selenium.webdriver.common.keys import Keys
                    active = driver.switch_to.active_element
                    if active and getattr(active, "tag_name", "") == "input":
                        active.send_keys(Keys.ENTER)
                except Exception:
                    pass
    # 超时仍停留验证码页：绝不能返回 accepted！否则后方资料页必然超时自杀
    if _is_email_verification_page(driver):
        has_error_mark = bool(last.get('errors')) or any(
            str(i.get('ariaInvalid') or '').lower() == 'true' for i in (last.get('inputs') or [])
        )
        if has_error_mark:
            logger.warning("%s[OTP] 提交等待超时仍停留验证码页且存在错误标记，按验证码无效处理 snapshot=%s", _log_prefix(driver), last)
            return 'invalid'
        logger.warning(
            "%s[OTP] 提交后 %ss 页面完全未跳转（仍停留在验证码页），判定为提交未生效，准备重发/重输 snapshot=%s",
            _log_prefix(driver), timeout, last
        )
        return 'invalid'
    return 'accepted'

