# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import re
import time
from typing import Callable

from .human_actions import _log_prefix, _page_snapshot

logger = logging.getLogger(__name__)


class AlreadyRegisteredError(Exception):
    pass


class UserAlreadyExistsError(AlreadyRegisteredError):
    """OpenAI 提示此邮箱已存在账号: user_already_exists"""
    pass


class PageLoadTimeoutError(TimeoutError):
    pass


class PageStageAdvanced(RuntimeError):
    """页面在找邮箱/填邮箱阶段已异步跳转到后续阶段（如 OTP、密码、资料或已登录）。"""
    def __init__(self, stage: str, details: dict | None = None):
        super().__init__(f"页面已进入后续认证阶段: {stage}")
        self.stage = stage
        self.details = details or {}


def _check_already_verified(snap: dict) -> bool:
    text = str(snap.get("text") or "").lower()
    markers = [
        "already verified", "bereits verifiziert", "déjà vérifié",
        "ya ha sido verificada", "already registered", "已经验证",
        "已经注册", "email address verified", "e-mail-adresse verifiziert",
        "user_already_exists", "account already exists", "already exists for this email",
        "please log in instead", "此电子邮件地址或电话号码已有关联的账户", "请改用登录",
        "すでにアカウントが存在します", "ログインしてください",
    ]
    return any(m in text for m in markers)


def _is_oauth_consent_like(driver) -> bool:
    """检测是否已到 OAuth 授权/consent 页。这里不能再点任何邮箱分支或全局提交按钮。"""
    try:
        return bool(driver.execute_script(r"""
        const url = String(location.href || '').toLowerCase();
        if (/oauth|authorize|consent/.test(url) && !/login|signup|identifier|email-verification/.test(url)) return true;
        const formsWithEmail = [...document.querySelectorAll('form')]
          .some(form => form.querySelector('input[type="email"],input[name="email"],input[name="username"],input[autocomplete="email"]'));
        if (formsWithEmail) return false;
        const actions = [...document.querySelectorAll('button,a,[role="button"],input[type="submit"],input[type="button"]')]
          .map(el => [el.id, el.name, el.type, el.getAttribute('data-testid'), el.getAttribute('data-test-id'),
            el.getAttribute('data-provider'), el.getAttribute('data-auth-provider'), el.getAttribute('href'),
            el.getAttribute('formaction'), el.value, el.className].filter(Boolean).join(' ').toLowerCase())
          .join(' ');
        return /oauth|authorize|consent|grant|allow/.test(actions) && !/email|username/.test(actions);
        """))
    except Exception:
        return False


def _is_external_idp_url(url: str) -> bool:
    u = str(url or '').lower()
    return any(x in u for x in (
        'accounts.google.', 'google.com/o/oauth', 'appleid.apple.', 'login.microsoftonline.',
        'login.live.', 'github.com/login/oauth', 'facebook.com/', 'saml', 'sso'
    ))


def _assert_not_external_idp(driver, label: str = '') -> None:
    try:
        current = str(driver.current_url or '')
    except Exception:
        current = ''
    if _is_external_idp_url(current):
        raise RuntimeError(f"误入第三方账号授权页（{label}）：{current}")


def _password_page_state(driver) -> dict:
    try:
        return driver.execute_script(r"""
        const visible = el => !!el && !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
          && getComputedStyle(el).visibility !== 'hidden' && getComputedStyle(el).display !== 'none'
          && !el.disabled && !el.readOnly;
        const inputs = [...document.querySelectorAll('input')].map(el => ({
          type: el.getAttribute('type') || '', name: el.getAttribute('name') || '', id: el.id || '',
          autocomplete: el.getAttribute('autocomplete') || '', visible: visible(el), value: el.type === 'password' ? '<password>' : (el.value || '')
        })).slice(0, 30);
        const forms = [...document.querySelectorAll('form')].map(f => ({action: f.getAttribute('action') || ''}));
        const buttons = [...document.querySelectorAll('button,input[type="submit"]')].map(el => ({
          type: el.getAttribute('type') || '', name: el.getAttribute('name') || '', id: el.id || '',
          disabled: !!el.disabled, visible: !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)
        })).slice(0, 30);
        return {url: location.href, inputs, forms, buttons};
        """) or {}
    except Exception as exc:
        return {"url": getattr(driver, "current_url", ""), "error": f"{type(exc).__name__}: {exc}"}


def _email_otp_page_state(driver) -> dict:
    try:
        return driver.execute_script(r"""
        const visible = el => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length || el.getAttribute('autocomplete') === 'one-time-code' || el.getAttribute('name') === 'code'));
        const inputs = [...document.querySelectorAll('input')].filter(visible).map(el => ({
          type: el.getAttribute('type') || '', name: el.getAttribute('name') || '', id: el.id || '',
          autocomplete: el.getAttribute('autocomplete') || '', inputmode: el.getAttribute('inputmode') || '',
          placeholder: el.getAttribute('placeholder') || '', ariaLabel: el.getAttribute('aria-label') || '',
          ariaInvalid: el.getAttribute('aria-invalid') || '', value: el.value || ''
        }));
        const buttons = [...document.querySelectorAll('button,a,[role=button],input[type=button],input[type=submit]')].filter(visible).map(el => ({
          tag: el.tagName, type: el.getAttribute('type') || '', value: el.getAttribute('value') || '',
          action: el.getAttribute('data-dd-action-name') || '', aria: el.getAttribute('aria-label') || '',
          disabled: !!el.disabled || String(el.getAttribute('aria-disabled') || '').toLowerCase() === 'true',
          text: (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 120)
        }));
        const errors = [...document.querySelectorAll('.react-aria-FieldError,[slot="errorMessage"],[id$="-error"],[aria-invalid="true"] + *,[class*="error"]')]
          .filter(visible).map(el => (el.innerText || el.textContent || '').replace(/\s+/g, ' ').trim()).filter(Boolean);
        return {url: location.href, title: document.title, inputs, buttons, errors, text: (document.body?.innerText || '').slice(0, 1200)};
        """) or {}
    except Exception as exc:
        return {"url": getattr(driver, 'current_url', ''), "error": f"{type(exc).__name__}: {exc}"}


def _is_user_already_exists_page(driver) -> bool:
    """检测当前页面是否出现 OpenAI 的 user_already_exists / account already exists 错误。"""
    try:
        return bool(driver.execute_script(r"""
        const text = (document.body?.innerText || document.body?.textContent || '').toLowerCase();
        if (text.includes('user_already_exists')) return true;
        if (text.includes('account already exists') && (text.includes('log in') || text.includes('try again'))) return true;
        if (text.includes('已有关联的账户') || (text.includes('账号已存在') && text.includes('登录'))) return true;
        if (text.includes('すでにアカウントが存在') || text.includes('アカウントが既に存在')) return true;
        return false;
        """))
    except Exception:
        return False


def _detect_current_auth_stage(driver) -> str:
    """全面诊断当前认证页面所处的具体阶段（OTP/CF盾/密码/登录态/邮箱页等）。"""
    try:
        url = str(getattr(driver, "current_url", "") or "").lower()
    except Exception:
        url = ""

    # 1. 登录成功态优先（必须真实持有 accessToken 或有已登录用户专有元素，严禁误判匿名访客页）
    try:
        from .session_step import _has_access_token
        if _has_access_token(driver):
            return "logged_in"
    except Exception:
        pass

    if "chatgpt.com" in url and not any(x in url for x in ("/auth/", "/login", "/api/auth/")):
        try:
            has_user_ui = bool(driver.execute_script(r"""
            return Boolean(
              document.querySelector('[data-testid="profile-button"]') ||
              document.querySelector('button[id*="headlessui-menu-button"]') ||
              document.querySelector('nav [data-testid*="user"]') ||
              document.querySelector('[data-testid="accounts-profile-button"]')
            );
            """))
            if has_user_ui:
                return "logged_in"
        except Exception:
            pass

    # 2. 邮箱已存在/已注册错误页 (user_already_exists)
    try:
        if _is_user_already_exists_page(driver):
            return "user_already_exists"
    except Exception:
        pass

    # 3. 登录密码页（已注册邮箱判定）
    try:
        if _is_login_password_page(driver):
            return "login_password"
    except Exception:
        pass

    # 3. 邮箱验证码页（新版注册核心关键路径）
    try:
        if _is_email_verification_page(driver):
            return "otp"
    except Exception:
        pass

    # 4. 新账号密码设置页
    try:
        if _is_signup_password_page(driver):
            return "password"
    except Exception:
        pass

    # 5. 个人资料页 (about-you / profile)
    if any(x in url for x in ("about-you", "profile", "create-account/about")):
        return "profile"

    # 6. Cloudflare 人机验证盾 / Turnstile 质询
    try:
        from core.cloudflare_turnstile import is_cloudflare_challenge
        if is_cloudflare_challenge(driver):
            return "cloudflare_challenge"
    except Exception:
        pass

    # 7. 邮箱输入页（若存在可见且未禁用的邮箱输入框）
    try:
        from .email_step import _find_visible_email_input_js
        if _find_visible_email_input_js(driver) is not None:
            return "email_page"
    except Exception:
        pass

    return "unknown"


def _is_signup_password_page(driver) -> bool:
    state = _password_page_state(driver)
    url = str(state.get('url') or '').lower()
    if any(x in url for x in ('/create-account/password', '/u/signup/password', '/signup/password')):
        return True
    if '/log-in/password' in url:
        return False
    inputs = state.get('inputs') or []
    return any(
        i.get('visible') and (
            str(i.get('type') or '').lower() == 'password'
            or 'password' in str(i.get('name') or '').lower()
            or str(i.get('autocomplete') or '').lower() == 'new-password'
        )
        for i in inputs
    )


def _is_login_password_page(driver) -> bool:
    try:
        url = str(driver.current_url or '').lower()
    except Exception:
        url = ''
    if '/log-in/password' in url:
        return True
    state = _password_page_state(driver)
    url = str(state.get('url') or '').lower()
    return '/log-in/password' in url


def _is_email_verification_page(driver) -> bool:
    try:
        url = str(getattr(driver, 'current_url', '') or '').lower()
    except Exception:
        url = ''
    state = _email_otp_page_state(driver)
    state_url = str(state.get("url") or '').lower()
    full_url = f"{url} {state_url}"
    if '/log-in/password' in full_url or 'about-you' in full_url or '/profile' in full_url:
        return False

    # 1. URL 强特征判断（auth.openai.com/email-verification 等）
    if any(p in full_url for p in (
        "email-verification", "/email_verification", "email_otp",
        "/verify-email", "verify_email", "/verify"
    )):
        return True

    inputs = state.get("inputs") or []

    # 2. 6 独立分格输入框 (PinField / 离散数字输入)
    pin_boxes = [
        i for i in inputs
        if (
            str(i.get("inputmode") or "").lower() == "numeric"
            or str(i.get("autocomplete") or "").lower() == "one-time-code"
            or "digit" in str(i.get("id") or "").lower()
            or "code" in str(i.get("name") or "").lower()
            or str(i.get("maxlength") or "") == "1"
        )
    ]
    if (4 <= len(pin_boxes) <= 8) or (len(inputs) == 6 and any(i.get("inputmode") == "numeric" or i.get("maxlength") == "1" for i in inputs)):
        return True

    # 3. 输入框技术属性 (one-time-code / otp / code / digit)
    attrs = ' '.join(' '.join(str(i.get(k) or '') for k in ('type', 'name', 'id', 'autocomplete', 'inputmode', 'placeholder', 'ariaLabel')) for i in inputs).lower()
    if any(k in attrs for k in ('one-time-code', 'otp', 'code', 'digit')):
        return True

    # 4. 专属按钮特征（重发邮件/使用密码等）
    buttons = state.get("buttons") or []
    btn_text = " ".join(str(b.get("text") or "") for b in buttons).lower()
    if any(k in btn_text for k in ("resend", "重新发送", "再送信", "reenviar", "renvoyer", "erneut senden", "재전송", "повторно")):
        return True

    # 5. 多语种页面正文特征匹配
    text = str(state.get("text") or "").lower()
    otp_text_patterns = [
        # 英文
        "check your inbox", "enter the verification code", "verification code",
        "enter the code", "code we just sent", "we sent a code", "resend email", "resend code", "use password instead",
        # 中文 (简/繁)
        "查看您的收件箱", "查看您的收件匣", "输入验证码", "輸入驗證碼", "验证码", "驗證碼", "重新发送电子邮件", "重新发送", "我们刚刚向", "发送了代码",
        # 日文
        "受信トレイを確認", "確認コードを入力", "確認コード", "認証コード", "コードを入力", "再送信", "パスワードで続行",
        # 韩文
        "수신함 확인", "인증 코드", "인증 코드를 입력", "이메일 재전송",
        # 西班牙文
        "código de verificación", "revisa tu bandeja de entrada", "introduce el código", "reenviar correo",
        # 法文
        "code de vérification", "boîte de réception", "saisissez le code", "renvoyer l'e-mail",
        # 德文
        "bestätigungscode", "e-mail-postfach", "code eingeben", "code erneut senden",
        # 俄文
        "код подтверждения", "проверьте входящие", "введите код", "отправить повторно",
        # 葡萄牙文
        "código de verificação", "caixa de entrada", "insira o código", "reenviar e-mail",
    ]
    if any(p in text for p in otp_text_patterns):
        return True

    return False


def _check_manual_stop() -> None:
    try:
        from core.registration_service import check_stop_requested
        check_stop_requested()
    except ImportError:
        return

