# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import random
import time
import uuid
from typing import Callable

from config import roxybrowser as _cfg
from core.humanize import delay as human_delay
from core.white_screen_guard import is_white_screen, wait_with_white_screen_guard
from .human_actions import (
    _log_prefix, _human_click, _human_type_text, _find_any, _click_any,
    _type_any, _page_snapshot, _click_continue, _maybe_accept, _handle_blank_page,
    _browser_actions_enabled,
)
from .stages import (
    _detect_current_auth_stage, _is_signup_password_page, _is_login_password_page,
    _is_email_verification_page, _check_already_verified, _check_manual_stop,
    _is_oauth_consent_like, _assert_not_external_idp, AlreadyRegisteredError,
    UserAlreadyExistsError, _is_user_already_exists_page, PageStageAdvanced,
)

logger = logging.getLogger(__name__)

_EMAIL_INPUT_SELECTORS = [
    "input[type='email']",
    "input[name='email']",
    "input[name='username']",
    "input#email-input",
    "input[autocomplete='email']",
]


def _email_entry_state(driver) -> dict:
    try:
        return driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
          && !el.disabled;
        const attrText = el => [
          el.id, el.getAttribute('name'), el.getAttribute('type'), el.getAttribute('autocomplete'),
          el.getAttribute('data-testid'), el.getAttribute('data-test-id'), el.getAttribute('data-provider'),
          el.getAttribute('data-auth-provider'), el.getAttribute('href'), el.getAttribute('action'),
          el.getAttribute('formaction'), el.getAttribute('value')
        ].filter(Boolean).join(' ').toLowerCase();
        const inputs = [...document.querySelectorAll('input')].filter(visible).map(el => ({
          type: el.getAttribute('type') || '', name: el.getAttribute('name') || '', id: el.id || '',
          autocomplete: el.getAttribute('autocomplete') || '', value: el.value || ''
        })).slice(0, 30);
        const actions = [...document.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]')]
          .filter(visible).map(el => ({tag: el.tagName, type: el.getAttribute('type') || '', attrs: attrText(el)})).slice(0, 40);
        return {url: location.href, title: document.title, inputs, actions, text: (document.body?.innerText || "").slice(0, 1200)};
        """) or {}
    except Exception as exc:
        return {"url": getattr(driver, "current_url", ""), "error": f"{type(exc).__name__}: {exc}"}


def _find_visible_email_input_js(driver):
    return driver.execute_script(r"""
    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
      && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
      && !el.disabled && !el.readOnly;
    const selectors = [
      'input[type="email"]',
      'input[name="email"]',
      'input[name="username"]',
      'input#email-input',
      'input[autocomplete="email"]'
    ];
    for (const sel of selectors) {
      const el = [...document.querySelectorAll(sel)].find(visible);
      if (el) return el;
    }
    return null;
    """)


def _click_email_entry_option(driver) -> bool:
    """点击“邮箱方式”入口；只看 DOM 技术属性，不看按钮可见文案，并显式排除 Google 等第三方。"""
    if _is_oauth_consent_like(driver):
        logger.info("%s 当前疑似 OAuth 授权页，跳过邮箱入口兜底点击", _log_prefix(driver))
        return False
    target = driver.execute_script(r"""
    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
      && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
      && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
    const attrText = el => {
      const own = [
        el.id, el.getAttribute('name'), el.getAttribute('type'), el.getAttribute('autocomplete'),
        el.getAttribute('data-testid'), el.getAttribute('data-test-id'), el.getAttribute('data-provider'),
        el.getAttribute('data-auth-provider'), el.getAttribute('data-idp'), el.getAttribute('href'), el.getAttribute('action'),
        el.getAttribute('formaction'), el.getAttribute('value'), el.getAttribute('aria-label'), el.className
      ].filter(Boolean).join(' ');
      const desc = [...el.querySelectorAll('img,svg,use,[aria-label],[data-provider],[data-testid],[data-test-id]')]
        .map(x => [x.getAttribute('alt'), x.getAttribute('src'), x.getAttribute('href'), x.getAttribute('xlink:href'),
          x.getAttribute('aria-label'), x.getAttribute('data-provider'), x.getAttribute('data-testid'), x.getAttribute('data-test-id'), x.className]
          .filter(Boolean).join(' ')).join(' ');
      return `${own} ${desc}`.toLowerCase();
    };
    const bad = /google|apple|microsoft|github|facebook|saml|sso|oauth|social|oidc|idp|provider|authorize|consent|grant|allow/;
    const good = /(^|[^a-z])(email|mail|username|passwordless|otp|magic)([^a-z]|$)/;
    const candidates = [...document.querySelectorAll('button,a,[role="button"],input[type="button"],input[type="submit"]')]
      .filter(visible)
      .map(el => ({el, attrs: attrText(el), hasLogo: !!el.querySelector('img,svg,use')}))
      .filter(x => good.test(x.attrs) && !bad.test(x.attrs) && !x.hasLogo);
    if (candidates.length !== 1) return null;
    candidates[0].el.scrollIntoView({block:'center'});
    return candidates[0].el;
    """)
    if target:
        _human_click(driver, target, label="email_entry")
        return True
    return False


def _wait_for_email_input(driver, timeout: int | None = None):
    """进入邮箱登录/注册方式并返回已找到的可见邮箱输入框。"""
    end = time.time() + (timeout or int(_cfg.ROXY_SELENIUM_TIMEOUT))
    last_state = None
    clicked_email_option = False
    blank_start_time = None
    cf_handled_in_wait = False
    missing_email_start: float | None = None
    refreshed_count = 0
    max_refreshes = 2
    while time.time() < end:
        el = _find_visible_email_input_js(driver)
        if el:
            return el

        # ★ 双向检查守卫：若页面已异步跳转到后续阶段，绝不继续在验证码页死等邮箱框
        stage = _detect_current_auth_stage(driver)
        if stage in ("user_already_exists", "already_registered"):
            logger.warning("%s 寻找邮箱输入框时检测到账号已存在 (user_already_exists)", _log_prefix(driver))
            raise UserAlreadyExistsError("寻找邮箱输入框时检测到账号已存在: user_already_exists")
        if stage in ("otp", "password", "login_password", "logged_in", "profile"):
            logger.info(
                "%s 寻找邮箱输入框时检测到页面已提前进入：%s，立即中断找邮箱并交接流程",
                _log_prefix(driver), stage
            )
            raise PageStageAdvanced(stage, details={"url": getattr(driver, "current_url", "")})

        if stage == "cloudflare_challenge" and not cf_handled_in_wait:
            logger.info("%s 寻找邮箱输入框时检测到处于 Cloudflare 质询，尝试清障等待...", _log_prefix(driver))
            cf_handled_in_wait = True
            try:
                from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile
                solve_or_wait_cloudflare_turnstile(driver, timeout=25)
            except Exception as exc:
                logger.debug("%s 寻找邮箱输入框阶段 CF 清障异常: %s", _log_prefix(driver), exc)
            continue

        last_state = _email_entry_state(driver)
        blank_start_time = _handle_blank_page(driver, last_state, blank_start_time)
        if not clicked_email_option and _click_email_entry_option(driver):
            clicked_email_option = True
            time.sleep(1.0)
            _assert_not_external_idp(driver, "点击邮箱入口后")
            continue

        # ★ 缺框/半挂载白板定向刷新自愈：处于登录/邮箱页但超 5.0 秒无邮箱输入框
        now = time.time()
        if missing_email_start is None:
            missing_email_start = now
        elif (now - missing_email_start) >= 5.0 and refreshed_count < max_refreshes:
            refreshed_count += 1
            logger.warning(
                "%s 寻找邮箱输入框超过 5.0 秒未果（页面可能卡在半挂载/骨架屏），主动执行第 %d/%d 次刷新自愈...",
                _log_prefix(driver), refreshed_count, max_refreshes
            )
            try:
                driver.refresh()
                time.sleep(2.0)
            except Exception as ref_exc:
                logger.warning("%s 邮箱页刷新自愈异常: %s", _log_prefix(driver), ref_exc)
            missing_email_start = None
            blank_start_time = None
            clicked_email_option = False
            continue

        time.sleep(0.4)

    # 超时前做最后一次状态诊断兜底
    stage_final = _detect_current_auth_stage(driver)
    if stage_final in ("otp", "password", "login_password", "logged_in", "profile"):
        logger.info("%s 寻找邮箱输入框超时前检测到页面实际处于：%s，交接流程", _log_prefix(driver), stage_final)
        raise PageStageAdvanced(stage_final, details=last_state)

    raise RuntimeError(f"找不到邮箱输入框/邮箱入口（未使用文字识别），state={last_state}")


def _type_email_address(driver, email: str, timeout: int | None = None) -> None:
    """进入邮箱登录/注册方式并填写邮箱。全程不依赖页面可见文字。"""
    el = _wait_for_email_input(driver, timeout=timeout)
    _human_type_text(driver, el, email, clear=True)


def _submit_nearest_form_for_active_input(driver) -> bool:
    if _is_oauth_consent_like(driver):
        logger.info("%s 当前疑似 OAuth 授权页，禁止执行邮箱提交", _log_prefix(driver))
        return False
    result = driver.execute_script(r"""
    const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
      && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
      && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
    const input = [...document.querySelectorAll('input[type="email"],input[name="email"],input[name="username"],input[autocomplete="email"]')]
      .find(visible);
    if (!input) return {ok:false, reason:'missing_email_input'};
    const value = String(input.value || '').trim();
    if (!value || !value.includes('@')) return {ok:false, reason:'email_value_not_ready', value};
    const form = input.closest('form');
    if (!form) return {ok:false, reason:'missing_form'};

    const bad = /google|apple|microsoft|github|facebook|saml|sso|oauth|social|oidc|sso|saml|idp|provider|authorize|consent|grant|allow/;
    const attrText = el => {
      const own = [el.id, el.name, el.type, el.getAttribute('data-testid'), el.getAttribute('data-test-id'),
        el.getAttribute('data-provider'), el.getAttribute('data-auth-provider'), el.getAttribute('data-idp'),
        el.getAttribute('aria-label'), el.getAttribute('href'), el.getAttribute('formaction'), el.value, el.className]
        .filter(Boolean).join(' ');
      const desc = [...el.querySelectorAll('img,svg,use,[aria-label],[data-provider],[data-testid],[data-test-id]')]
        .map(x => [x.getAttribute('alt'), x.getAttribute('src'), x.getAttribute('href'), x.getAttribute('xlink:href'),
          x.getAttribute('aria-label'), x.getAttribute('data-provider'), x.getAttribute('data-testid'), x.getAttribute('data-test-id'), x.className]
          .filter(Boolean).join(' '))
        .join(' ');
      return `${own} ${desc}`.toLowerCase();
    };
    const inputRect = input.getBoundingClientRect();
    const formId = form.getAttribute('id') || '';
    const scopedButtons = [
      ...form.querySelectorAll('button,input[type="submit"]'),
      ...(formId ? [...document.querySelectorAll(`button[form="${CSS.escape(formId)}"],input[type="submit"][form="${CSS.escape(formId)}"]`)] : [])
    ].filter((el, idx, arr) => arr.indexOf(el) === idx);
    const rawButtons = scopedButtons
      .filter(visible)
      .map((el, idx) => {
        const r = el.getBoundingClientRect();
        const attrs = attrText(el);
        const hasLogo = !!el.querySelector('img,svg,use');
        const isBad = bad.test(attrs) || hasLogo;
        const belowInput = r.top >= inputRect.bottom - 10;
        const distance = Math.max(0, r.top - inputRect.bottom) + Math.abs((r.left + r.right) / 2 - (inputRect.left + inputRect.right) / 2) / 10;
        const cls = String(el.className || '').toLowerCase();
        const type = String(el.getAttribute('type') || '').toLowerCase();
        // ChatGPT 新版邮箱页的主按钮形如：
        // <button class="... btn-primary ... w-full ..." type="submit"><div>続行</div></button>
        // 优先选择同 form 下的 primary submit，而不是因为多个按钮距离接近误判歧义。
        const isPrimarySubmit = (el.tagName === 'BUTTON' || el.tagName === 'INPUT') && type === 'submit'
          && (/\bbtn-primary\b/.test(cls) || /\b_primary_/.test(cls) || /\bw-full\b/.test(cls));
        const score = (isPrimarySubmit ? 1000 : 0) + (type === 'submit' ? 100 : 0) - distance;
        return {el, idx, attrs, isBad, hasLogo, belowInput, distance, score, isPrimarySubmit, tag: el.tagName, type};
      });
    const safe = rawButtons.filter(x => !x.isBad && x.belowInput)
      .sort((a,b) => b.score - a.score || a.distance - b.distance || a.idx - b.idx);
    if (!safe.length) {
      return {ok:false, reason:'no_safe_submit', buttons: rawButtons.map(x => ({idx:x.idx, isBad:x.isBad, hasLogo:x.hasLogo, belowInput:x.belowInput, primary:x.isPrimarySubmit, attrs:x.attrs.slice(0,160), type:x.type}))};
    }
    // 多个安全按钮时，若没有明确 primary submit，且距离接近，才认为页面歧义。
    if (!safe[0].isPrimarySubmit && safe.length > 1 && Math.abs(safe[0].distance - safe[1].distance) < 8) {
      return {ok:false, reason:'ambiguous_submit', buttons: safe.slice(0,3).map(x => ({idx:x.idx, distance:x.distance, score:x.score, primary:x.isPrimarySubmit, attrs:x.attrs.slice(0,160), type:x.type}))};
    }
    const target = safe[0].el;
    target.scrollIntoView({block:'center'});
    window.__roxy_email_submit_debug = {at: Date.now(), targetAttrs: safe[0].attrs.slice(0,240), buttonCount: rawButtons.length, primary:safe[0].isPrimarySubmit};
    return {ok:true, reason:safe[0].isPrimarySubmit ? 'primary_submit' : 'safe_submit', target, targetAttrs:safe[0].attrs.slice(0,160), primary:safe[0].isPrimarySubmit};
    """) or {}
    if result.get("ok"):
        target = result.get("target")
        if target:
            _human_click(driver, target, label="email_submit")
        else:
            logger.warning("%s 邮箱提交未返回目标元素，回退 requestSubmit", _log_prefix(driver))
            driver.execute_script("document.querySelector('form')?.requestSubmit?.();")
        logger.info("%s 邮箱表单安全提交：%s", _log_prefix(driver), result)
        time.sleep(0.8)
        _assert_not_external_idp(driver, "提交邮箱后")
        return True
    logger.warning("%s 未执行邮箱提交：%s", _log_prefix(driver), result)
    return False


def _current_email_input_value(driver) -> str:
    try:
        state = _email_input_value_state(driver)
        for item in state.get("inputs") or []:
            value = str(item.get("value") or "").strip()
            if "@" in value:
                return value
    except Exception:
        pass
    return ""


def _stabilize_email_input_before_submit(driver, email: str) -> dict:
    """提交前把 DOM value / React 受控状态 / blur-change 状态统一稳定下来。"""
    try:
        return driver.execute_script(r"""
        const email = String(arguments[0] || '').trim();
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
          && !el.disabled && !el.readOnly;
        const input = [...document.querySelectorAll('input[type="email"],input[name="email"],input[name="username"],input[autocomplete*="email"]')]
          .find(visible);
        if (!input) return {ok:false, reason:'missing_email_input'};

        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        input.scrollIntoView({block:'center', inline:'nearest'});
        input.focus();
        if (setter) setter.call(input, email); else input.value = email;

        // 让 React/表单校验尽量收到完整输入链路。
        try { input.dispatchEvent(new InputEvent('beforeinput', {bubbles:true, cancelable:true, inputType:'insertText', data:email})); } catch (_) {}
        try { input.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:email})); } catch (_) {
          input.dispatchEvent(new Event('input', {bubbles:true}));
        }
        input.dispatchEvent(new Event('change', {bubbles:true}));
        input.dispatchEvent(new FocusEvent('blur', {bubbles:true}));
        input.blur();
        input.focus();

        const form = input.closest('form');
        const submit = form?.querySelector('button[type="submit"],input[type="submit"]');
        return {
          ok:true,
          value: input.value,
          active: document.activeElement === input,
          hasForm: !!form,
          hasSubmit: !!submit,
          submitDisabled: submit ? (!!submit.disabled || String(submit.getAttribute('aria-disabled') || '').toLowerCase() === 'true') : null,
          url: location.href
        };
        """, email) or {}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _submit_email_form_stable(driver, email: str) -> dict:
    """第一次提交就按“补交成功”的方式执行：稳定 value 后 Enter + DOM click。"""
    try:
        return driver.execute_script(r"""
        const email = String(arguments[0] || '').trim();
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
          && !el.disabled && el.getAttribute('aria-disabled') !== 'true';
        const editable = el => visible(el) && !el.readOnly;
        const input = [...document.querySelectorAll('input[type="email"],input[name="email"],input[name="username"],input[autocomplete*="email"]')]
          .find(editable);
        if (!input) return {ok:false, reason:'missing_email_input'};
        if (!email || !email.includes('@')) return {ok:false, reason:'empty_email', value: email};

        const form = input.closest('form');
        if (!form) return {ok:false, reason:'missing_form'};

        const bad = /google|apple|microsoft|github|facebook|saml|sso|oauth|social|oidc|idp|provider|authorize|consent|grant|allow/;
        const attrText = el => {
          const own = [el.id, el.name, el.type, el.getAttribute('data-testid'), el.getAttribute('data-test-id'),
            el.getAttribute('data-provider'), el.getAttribute('data-auth-provider'), el.getAttribute('data-idp'),
            el.getAttribute('aria-label'), el.getAttribute('href'), el.getAttribute('formaction'), el.value, el.className]
            .filter(Boolean).join(' ');
          const desc = [...el.querySelectorAll('img,svg,use,[aria-label],[data-provider],[data-testid],[data-test-id]')]
            .map(x => [x.getAttribute('alt'), x.getAttribute('src'), x.getAttribute('href'), x.getAttribute('xlink:href'),
              x.getAttribute('aria-label'), x.getAttribute('data-provider'), x.getAttribute('data-testid'), x.getAttribute('data-test-id'), x.className]
              .filter(Boolean).join(' '))
            .join(' ');
          return `${own} ${desc}`.toLowerCase();
        };

        const formId = form.getAttribute('id') || '';
        const buttons = [
          ...form.querySelectorAll('button,input[type="submit"]'),
          ...(formId ? [...document.querySelectorAll(`button[form="${CSS.escape(formId)}"],input[type="submit"][form="${CSS.escape(formId)}"]`)] : [])
        ].filter((el, idx, arr) => arr.indexOf(el) === idx)
          .filter(el => visible(el) && !bad.test(attrText(el)) && !el.querySelector('img,svg,use'));
        const submit = buttons.find(el => (el.getAttribute('type') || '').toLowerCase() === 'submit') || buttons[0] || null;
        if (!submit) return {ok:false, reason:'missing_safe_submit'};

        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        input.scrollIntoView({block:'center', inline:'nearest'});
        input.focus();
        if (setter) setter.call(input, email); else input.value = email;
        try { input.dispatchEvent(new InputEvent('beforeinput', {bubbles:true, cancelable:true, inputType:'insertText', data:email})); } catch (_) {}
        try { input.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:email})); } catch (_) {
          input.dispatchEvent(new Event('input', {bubbles:true}));
        }
        input.dispatchEvent(new Event('change', {bubbles:true}));
        input.dispatchEvent(new FocusEvent('blur', {bubbles:true}));
        input.blur();
        input.focus();

        submit.scrollIntoView({block:'center', inline:'nearest'});

        // 不要在 execute_script 同步执行 submit.click()：
        // ChromeDriver 会等前端 submit/navigation，Roxy/Chrome 150 上可能卡到 page/script timeout。
        // setTimeout 让 Selenium 先返回，点击在页面事件循环里异步发生，和补交逻辑一致。
        setTimeout(() => {
          try {
            input.focus();
            input.dispatchEvent(new KeyboardEvent('keydown', {bubbles:true, cancelable:true, key:'Enter', code:'Enter'}));
            input.dispatchEvent(new KeyboardEvent('keypress', {bubbles:true, cancelable:true, key:'Enter', code:'Enter'}));
            input.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, cancelable:true, key:'Enter', code:'Enter'}));
            if (submit && !submit.disabled) submit.click();
            else if (form && typeof form.requestSubmit === 'function') form.requestSubmit();
          } catch (_) {}
        }, 80);

        window.__roxy_email_submit_debug = {
          at: Date.now(),
          mode: 'stable_async_enter_click',
          value: input.value,
          submitAttrs: attrText(submit).slice(0, 240)
        };
        return {
          ok:true,
          reason:'stable_async_enter_click',
          value: input.value,
          submitDisabled: !!submit.disabled || String(submit.getAttribute('aria-disabled') || '').toLowerCase() === 'true',
          submitAttrs: attrText(submit).slice(0, 180),
          url: location.href
        };
        """, email) or {}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _submit_email_step(driver, email: str | None = None) -> None:
    # 不再优先走浏览器内 NextAuth fetch：
    # Roxy/Chrome 150 下 execute_async_script + fetch 偶发卡到 script timeout；
    # 实测 UI 首次提交后若停在 /auth/login?email=...，由 _recover_email_submit_if_stuck 补交表单更稳定。
    email_value = str(email or _current_email_input_value(driver) or "").strip()
    stable = _stabilize_email_input_before_submit(driver, email_value)
    logger.info("%s 邮箱提交前状态稳定：%s", _log_prefix(driver), stable)
    time.sleep(random.uniform(0.8, 1.8) if _browser_actions_enabled() else 0.4)

    stable_submit = _submit_email_form_stable(driver, email_value)
    if stable_submit.get("ok"):
        logger.info("%s 邮箱稳定表单提交：%s", _log_prefix(driver), stable_submit)
        time.sleep(1.0)
        _assert_not_external_idp(driver, "稳定表单提交邮箱后")
        return
    logger.warning("%s 邮箱稳定表单提交失败，回退 UI 点击提交：%s", _log_prefix(driver), stable_submit)
    if _submit_nearest_form_for_active_input(driver):
        return
    raise RuntimeError(f"无法提交邮箱步骤（拒绝按页面文字或首个 submit 兜底，避免误点第三方登录），state={_email_entry_state(driver)}")


def _recover_email_submit_if_stuck(driver, email: str) -> dict:
    """邮箱提交后停在 /auth/login?email= 且输入框被清空时，补一次原生表单提交。"""
    try:
        return driver.execute_script(r"""
        const email = String(arguments[0] || '').trim();
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
          && !el.disabled && !el.readOnly;
        const input = [...document.querySelectorAll('input[type="email"],input[name="email"],input[name="username"],input[autocomplete*="email"]')]
          .find(visible);
        if (!input) return {ok:false, reason:'missing_email_input'};
        const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set;
        input.focus();
        if (setter) setter.call(input, email); else input.value = email;
        input.dispatchEvent(new InputEvent('input', {bubbles:true, inputType:'insertText', data:email}));
        input.dispatchEvent(new Event('change', {bubbles:true}));
        const form = input.closest('form');
        const submit = form?.querySelector('button[type="submit"],input[type="submit"]');
        setTimeout(() => {
          try {
            input.dispatchEvent(new KeyboardEvent('keydown', {bubbles:true, cancelable:true, key:'Enter', code:'Enter'}));
            input.dispatchEvent(new KeyboardEvent('keyup', {bubbles:true, cancelable:true, key:'Enter', code:'Enter'}));
            if (submit && !submit.disabled) submit.click();
            else if (form && typeof form.requestSubmit === 'function') form.requestSubmit();
          } catch (_) {}
        }, 80);
        return {ok:true, reason:'resubmitted_email_form', value: input.value, hasForm: !!form, hasSubmit: !!submit};
        """, email) or {}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}


def _submit_email_via_browser_nextauth(driver, email: str) -> dict:
    """在 Roxy 浏览器上下文里调用 ChatGPT NextAuth signin。

    UI submit 在 Roxy/Chrome 150 上会偶发只跳到 `/auth/login?email=...` 后停住。
    这里改走浏览器页面内 fetch，仍使用当前 Roxy 浏览器的 cookie / 指纹环境，
    拿到 auth.openai.com authorize URL 后让浏览器跳转。
    """
    try:
        current = str(getattr(driver, "current_url", "") or "")
        if "chatgpt.com" not in current:
            return {"ok": False, "reason": "not_on_chatgpt", "url": current[:180]}
    except Exception:
        current = ""

    did = str(uuid.uuid4())
    auth_log_id = str(uuid.uuid4())
    old_script_timeout = int(getattr(_cfg, "ROXY_SELENIUM_TIMEOUT", 90) or 90)
    try:
        try:
            driver.set_script_timeout(25)
        except Exception:
            pass
        result = driver.execute_async_script(r"""
        const email = String(arguments[0] || '').trim();
        const did = String(arguments[1] || '');
        const authLogId = String(arguments[2] || '');
        const done = arguments[arguments.length - 1];
        (async () => {
          try {
            const csrfResp = await fetch('/api/auth/csrf', {
              method: 'GET',
              credentials: 'include',
              headers: {
                'accept': 'application/json',
                'cache-control': 'no-cache',
                'pragma': 'no-cache'
              }
            });
            const csrfText = await csrfResp.text();
            let csrfData = {};
            try { csrfData = JSON.parse(csrfText); } catch (_) {}
            const csrfToken = csrfData.csrfToken || '';
            if (!csrfResp.ok || !csrfToken) {
              done({ok:false, stage:'csrf', status:csrfResp.status, body:csrfText.slice(0, 500)});
              return;
            }

            const q = new URLSearchParams({
              prompt: 'login',
              'ext-oai-did': did,
              auth_session_logging_id: authLogId,
              'ext-passkey-client-capabilities': '11111',
              screen_hint: 'login_or_signup',
              login_hint: email
            });
            const body = new URLSearchParams({
              callbackUrl: 'https://chatgpt.com/',
              csrfToken,
              json: 'true'
            });
            const resp = await fetch('/api/auth/signin/openai?' + q.toString(), {
              method: 'POST',
              credentials: 'include',
              headers: {
                'accept': 'application/json',
                'content-type': 'application/x-www-form-urlencoded',
                'cache-control': 'no-cache',
                'pragma': 'no-cache'
              },
              body: body.toString()
            });
            const text = await resp.text();
            let data = {};
            try { data = JSON.parse(text); } catch (_) {}
            let url = data.url || '';
            if (!resp.ok || !url) {
              done({ok:false, stage:'signin', status:resp.status, body:text.slice(0, 700)});
              return;
            }

            try {
              const u = new URL(url, location.href);
              if (!u.searchParams.get('screen_hint')) u.searchParams.set('screen_hint', 'login_or_signup');
              if (!u.searchParams.get('login_hint')) u.searchParams.set('login_hint', email);
              if (!u.searchParams.get('ext-oai-did')) u.searchParams.set('ext-oai-did', did);
              if (!u.searchParams.get('auth_session_logging_id')) u.searchParams.set('auth_session_logging_id', authLogId);
              url = u.toString();
            } catch (_) {}
            window.location.assign(url);
            done({ok:true, stage:'redirect', url:url.slice(0, 260)});
          } catch (e) {
            done({ok:false, stage:'exception', error:String(e && (e.stack || e.message) || e).slice(0, 700)});
          }
        })();
        """, email, did, auth_log_id) or {}
        return result if isinstance(result, dict) else {"ok": False, "reason": "invalid_result", "result": str(result)[:300]}
    except Exception as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    finally:
        try:
            driver.set_script_timeout(old_script_timeout)
        except Exception:
            pass


def _email_input_value_state(driver) -> dict:
    """读取当前可见邮箱框状态，用于提交后确认是否真的进入下一步。"""
    try:
        return driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
          && !el.disabled && !el.readOnly;
        const inputs = [...document.querySelectorAll('input[type="email"],input[name="email"],input[name="username"],input[autocomplete*="email"]')]
          .filter(visible)
          .map(el => ({type: el.getAttribute('type') || '', name: el.name || '', id: el.id || '', autocomplete: el.getAttribute('autocomplete') || '', value: el.value || ''}));
        return {url: location.href, inputs, text: (document.body?.innerText || "").slice(0, 1200)};
        """) or {}
    except Exception as exc:
        return {"url": getattr(driver, "current_url", ""), "error": f"{type(exc).__name__}: {exc}"}


def _is_email_login_page_still_present(driver) -> bool:
    state = _email_input_value_state(driver)
    return bool(state.get("inputs"))


def _wait_email_submit_next_state(driver, email: str, timeout: int = 25) -> str:
    """邮箱提交后等待进入 password / otp / logged_in；仍停留邮箱页则返回 email_page。

    Cloak/Playwright 路径里，点击 submit 后页面经常先发生一次 SPA 导航：
    `chatgpt.com/auth/login?email=...`，同时 React 会短暂把 email input 清空。
    旧逻辑一看到空 input 就立刻返回 `email_cleared`，导致在真正跳到
    `auth.openai.com/...` 前过早重填，形成“提交 -> 清空 -> 重填”的循环。
    这里对 email_cleared 做去抖：只记录并继续观察几秒；若期间进入
    password/otp/login_password/logged_in 则按真实状态返回，持续清空才让上层重试。
    """
    end = time.time() + timeout
    last = None
    cleared_seen_at: float | None = None
    cleared_last_log_at = 0.0
    cleared_recover_done = False
    blank_start_time = None
    cf_handled = False
    unknown_start: float | None = None
    refreshed_count = 0
    max_refreshes = 2
    expected_email = str(email or "").strip().lower()
    while time.time() < end:
        stage = _detect_current_auth_stage(driver)
        if stage == "logged_in":
            return "logged_in"
        if stage == "login_password":
            return "login_password"
        if stage == "otp":
            return "otp"
        if stage == "password":
            return "password"
        if stage in ("user_already_exists", "already_registered"):
            logger.warning("%s 邮箱提交后检测到页面处于已存在账号错误页 (user_already_exists)", _log_prefix(driver))
            raise UserAlreadyExistsError("邮箱提交后检测到账号已存在: user_already_exists")

        # ★ 遇到 Cloudflare 人机质询时的自动清障与动态延时
        if stage == "cloudflare_challenge" and not cf_handled:
            logger.info("%s 邮箱提交后检测到 Cloudflare 人机质询，启动自动清障并延长观察窗口...", _log_prefix(driver))
            cf_handled = True
            end = max(end, time.time() + 45)
            try:
                from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile
                solve_or_wait_cloudflare_turnstile(driver, timeout=35)
            except Exception as exc:
                logger.warning("%s Cloudflare 清障尝试异常: %s", _log_prefix(driver), exc)
            continue

        state = _email_input_value_state(driver)
        last = state
        blank_start_time = _handle_blank_page(driver, state, blank_start_time)
        if _check_already_verified(state) or _is_user_already_exists_page(driver):
            raise UserAlreadyExistsError("检测到邮箱已存在/已验证页面 (user_already_exists)，需切换至登录或标记停用。")
        inputs = state.get("inputs") or []
        if inputs:
            unknown_start = None
            values = [str(i.get("value") or "") for i in inputs]
            url = str(state.get("url") or "")
            has_blank = any(v == "" for v in values)
            has_expected = any(v.strip().lower() == expected_email for v in values)
            if has_blank and not has_expected:
                now = time.time()
                if cleared_seen_at is None:
                    cleared_seen_at = now
                # URL 已带 email 查询参数时更像是提交后的中间态，给它更长观察窗口。
                debounce = 18.0 if ("/auth/login" in url and "email=" in url) else 5.0
                if now - cleared_last_log_at > 2.0:
                    logger.info(
                        "%s 邮箱提交后检测到输入框短暂清空，继续等待跳转：elapsed=%.1fs debounce=%.1fs url=%s",
                        _log_prefix(driver), now - cleared_seen_at, debounce, url[:180],
                    )
                    cleared_last_log_at = now
                if (
                    not cleared_recover_done
                    and "/auth/login" in url
                    and "email=" in url
                    and now - cleared_seen_at >= 2.0
                ):
                    recover = _recover_email_submit_if_stuck(driver, email)
                    cleared_recover_done = True
                    logger.info("%s 邮箱提交后仍停留在 login?email，中途补交一次表单：%s", _log_prefix(driver), recover)
                if now - cleared_seen_at >= debounce:
                    return "email_cleared"
            else:
                cleared_seen_at = None
            # 仍是当前邮箱页，继续短等。
        else:
            # ★ 提交过渡未知白板定向刷新自愈：页面无输入框且处于未知挂起状态超过 6.0 秒
            now = time.time()
            if unknown_start is None:
                unknown_start = now
            elif (now - unknown_start) >= 6.0 and refreshed_count < max_refreshes:
                refreshed_count += 1
                logger.warning(
                    "%s 邮箱提交后超过 6.0 秒处于未知未挂载状态，主动执行第 %d/%d 次刷新自愈...",
                    _log_prefix(driver), refreshed_count, max_refreshes
                )
                try:
                    driver.refresh()
                    time.sleep(2.0)
                except Exception as ref_exc:
                    logger.warning("%s 提交后刷新自愈异常: %s", _log_prefix(driver), ref_exc)
                unknown_start = None
                blank_start_time = None
                continue

        time.sleep(0.8)

    # 超时前最后一次诊断兜底
    stage_final = _detect_current_auth_stage(driver)
    if stage_final in ("otp", "password", "login_password", "logged_in"):
        logger.info("%s 邮箱提交等待超时前诊断到页面实际处于：%s，成功流转", _log_prefix(driver), stage_final)
        return stage_final
    if stage_final in ("user_already_exists", "already_registered"):
        raise UserAlreadyExistsError("邮箱提交后检测到账号已存在: user_already_exists")

    logger.info("%s 邮箱提交后等待下一步超时，最后邮箱页状态=%s", _log_prefix(driver), last)
    return "email_page" if _is_email_login_page_still_present(driver) else "unknown"


def _submit_email_and_wait_next(
    driver,
    email: str | None,
    attempts: int = 3,
    email_supplier: Callable[[], str] | None = None,
) -> str:
    """填写并提交邮箱，必须确认进入 password/otp/logged_in 才返回。"""
    last_state = None
    current_email = str(email or "").strip()
    for attempt in range(1, attempts + 1):
        # 1. 前置双向检查：当前是否已经跨越到后续步骤（避免重试时盲目再输邮箱）
        pre_stage = _detect_current_auth_stage(driver)
        if pre_stage in ("password", "otp", "logged_in"):
            logger.info("%s 准备填写邮箱（attempt=%s/%s）前检测到页面已在后续阶段：%s，直接流转下一步", _log_prefix(driver), attempt, attempts, pre_stage)
            return pre_stage
        if pre_stage in ("user_already_exists", "already_registered"):
            raise UserAlreadyExistsError(f"填写邮箱前检测到账号已存在: url={getattr(driver, 'current_url', '') or 'user_already_exists'}")
        if pre_stage == "login_password":
            raise RuntimeError(f"邮箱提交后进入登录密码页，按已注册/不可用邮箱处理并停用: url={getattr(driver, 'current_url', '') or 'https://auth.openai.com/log-in/password'}")
        if pre_stage == "cloudflare_challenge":
            logger.info("%s 填写邮箱前检测到处于 Cloudflare 质询，先执行清障等待...", _log_prefix(driver))
            try:
                from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile
                solve_or_wait_cloudflare_turnstile(driver, timeout=35)
            except Exception as e:
                logger.warning("%s 前置 CF 清障异常: %s", _log_prefix(driver), e)
            post_cf = _detect_current_auth_stage(driver)
            if post_cf in ("password", "otp", "logged_in"):
                logger.info("%s Cloudflare 质询放行后页面已进入下一步：%s", _log_prefix(driver), post_cf)
                return post_cf
            if post_cf in ("user_already_exists", "already_registered"):
                raise UserAlreadyExistsError("Cloudflare 质询后进入账号已存在页面 (user_already_exists)")
            if post_cf == "login_password":
                raise RuntimeError("Cloudflare 质询后进入登录密码页，按已注册邮箱处理")

        # 2. 填写邮箱（捕获 PageStageAdvanced 异常实现平滑流转）
        try:
            if current_email:
                _type_email_address(driver, current_email, timeout=20)
            else:
                # 先确认页面已有可用输入框，再领取邮箱；不能把领取动作放在页面导航之前。
                email_input = _wait_for_email_input(driver, timeout=20)
                if email_supplier is None:
                    raise RuntimeError("已找到邮箱输入框，但未提供邮箱分配器")
                current_email = str(email_supplier() or "").strip()
                if not current_email:
                    raise RuntimeError("邮箱分配器返回了空邮箱地址")
                _human_type_text(driver, email_input, current_email, clear=True)
        except PageStageAdvanced as pae:
            if pae.stage in ("user_already_exists", "already_registered"):
                raise UserAlreadyExistsError(f"邮箱环节页面检测到账号已存在: url={getattr(driver, 'current_url', '') or 'user_already_exists'}")
            if pae.stage == "login_password":
                raise RuntimeError(f"邮箱环节页面进入登录密码页，按已注册/不可用邮箱处理并停用: url={getattr(driver, 'current_url', '') or 'https://auth.openai.com/log-in/password'}")
            if pae.stage in ("password", "otp", "logged_in"):
                logger.info("%s 寻找/填写邮箱过程中检测到页面已提前进入：%s，直接流转下一步", _log_prefix(driver), pae.stage)
                return pae.stage
            raise

        state = _email_input_value_state(driver)
        last_state = state
        values = [str(i.get("value") or "") for i in (state.get("inputs") or [])]
        if not any(v.strip().lower() == current_email.lower() for v in values):
            # 在报错/重试前再次确认是否因页面跳转导致 value 不匹配
            check_stage = _detect_current_auth_stage(driver)
            if check_stage in ("password", "otp", "logged_in"):
                logger.info("%s 邮箱校验阶段检测到页面已进入下一步：%s", _log_prefix(driver), check_stage)
                return check_stage
            if check_stage in ("user_already_exists", "already_registered"):
                raise UserAlreadyExistsError("邮箱校验阶段检测到账号已存在 (user_already_exists)")
            logger.warning("%s 邮箱写入校验失败，准备重试：attempt=%s/%s state=%s", _log_prefix(driver), attempt, attempts, state)
            time.sleep(0.8)
            continue
        logger.info("%s 已填写邮箱并校验通过：%s", _log_prefix(driver), current_email)
        human_delay("form")
        _submit_email_step(driver, current_email)
        logger.info("%s 已提交邮箱，等待进入密码页或验证码页（%s/%s）", _log_prefix(driver), attempt, attempts)
        state_name = _wait_email_submit_next_state(driver, current_email, timeout=25)
        if state_name in ("user_already_exists", "already_registered"):
            raise UserAlreadyExistsError(f"邮箱提交后进入账号已存在页面: url={getattr(driver, 'current_url', '') or 'user_already_exists'}")
        if state_name == "login_password":
            raise RuntimeError(f"邮箱提交后进入登录密码页，按已注册/不可用邮箱处理并停用: url={getattr(driver, 'current_url', '') or 'https://auth.openai.com/log-in/password'}")
        if state_name in ("password", "otp", "logged_in"):
            logger.info("%s 邮箱提交后已进入下一步：%s", _log_prefix(driver), state_name)
            return state_name
        logger.warning("%s 邮箱提交后仍未进入下一步：%s，准备重填重试 state=%s", _log_prefix(driver), state_name, _email_input_value_state(driver))
        time.sleep(1.0)
    raise RuntimeError(f"邮箱提交后未进入密码页/验证码页，最后状态={last_state}")

