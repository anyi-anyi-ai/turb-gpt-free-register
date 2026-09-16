# -*- coding: utf-8 -*-
"""通过 CloakBrowser + Playwright 适配层执行 ChatGPT 注册。"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Callable

from config import cloakbrowser as _cfg
from config import twofa as _twofa_cfg
from core.account_export import save_account_data, post_register_dwell
from core.browser_data_saver import BrowserDataSaver
from core.browser_traffic import PlaywrightTrafficTracker
from core.cloakbrowser_driver import build_cloak_driver
from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile, VerificationTimeoutError
from core.email_provider import acquire_email_after_input, wait_for_otp, resolve_email_source
from core.humanize import delay as human_delay

# 复用 Roxy 注册流程里已维护好的页面操作函数。
from core.roxy_registration import (  # noqa: F401
    _safe_get, _maybe_accept, _submit_email_and_wait_next, _fill_password_page_if_present,
    _clear_otp_inputs, _type_otp, _click_continue, _wait_after_email_otp_submit,
    _click_resend_email_otp, _complete_profile_page, _fetch_chatgpt_session, _check_manual_stop,
)

logger = logging.getLogger(__name__)


def setup_cloak_inbrowser_2fa(driver, email: str) -> tuple[str | None, str | None]:
    """
    在当前已登录 ChatGPT 的 CloakBrowser 浏览器实例内直接执行 2FA 重认证与 TOTP 激活。
    完全复用浏览器当前的 Cloudflare clearance 与 会话 Cookie，免遭外部 403 阻断。
    返回 (totp_secret, fresh_access_token)。
    若失败则返回 (None, None)，不影响注册结果。
    """
    import pyotp
    from core.email_provider import wait_for_otp

    logger.info("=" * 50)
    logger.info("[Cloak 2FA] 开始在浏览器内自动执行 2FA (TOTP) 设置: %s", email)
    logger.info("=" * 50)

    try:
        page = getattr(driver, "page", None)
        if page is None:
            raise RuntimeError("Cloak driver 缺少 page 属性")

        # 1. 在 chatgpt.com 发起重认证请求
        logger.info("[Cloak 2FA] 正在获取重认证 authorize URL...")
        reauth_res = page.evaluate("""async (email) => {
            try {
                const csrfRes = await fetch('/api/auth/csrf');
                if (!csrfRes.ok) return {ok: false, error: 'CSRF status ' + csrfRes.status};
                const csrfData = await csrfRes.json();

                const params = new URLSearchParams({
                    connection: 'password',
                    login_hint: email,
                    reauth: 'password',
                    max_age: '0'
                });
                const body = new URLSearchParams({
                    callbackUrl: 'https://chatgpt.com/?action=enable&factor=totp',
                    csrfToken: csrfData.csrfToken,
                    json: 'true'
                });
                const signinRes = await fetch('/api/auth/signin/openai?' + params.toString(), {
                    method: 'POST',
                    headers: {'Content-Type': 'application/x-www-form-urlencoded'},
                    body: body.toString()
                });
                if (!signinRes.ok) return {ok: false, error: 'signin/openai status ' + signinRes.status};
                const signinData = await signinRes.json();
                return {ok: true, url: signinData.url};
            } catch (e) {
                return {ok: false, error: String(e)};
            }
        }""", email)

        if not reauth_res.get("ok") or not reauth_res.get("url"):
            raise RuntimeError(f"获取重认证 URL 失败: {reauth_res}")

        auth_url = reauth_res["url"]
        logger.info("[Cloak 2FA] 已获取重认证 URL，正在导航并触发二次邮件 OTP...")
        reauth_ts = time.time()
        driver.get(auth_url)
        time.sleep(3)

        # 2. 等待接收 2FA OTP 邮件
        logger.info("[Cloak 2FA] 正在等待收取 2FA 邮件验证码...")
        otp_code = wait_for_otp(email, after_ts=reauth_ts, max_wait=120)
        logger.info("[Cloak 2FA] 已收到 2FA OTP 验证码: %s", otp_code)

        # 3. 提交 OTP 验证
        logger.info("[Cloak 2FA] 正在提交 2FA OTP 验证...")
        continue_url = None
        try:
            val_res = page.evaluate("""async (code) => {
                try {
                    const res = await fetch('/api/accounts/email-otp/validate', {
                        method: 'POST',
                        headers: {'Content-Type': 'application/json'},
                        body: JSON.stringify({code: code})
                    });
                    if (!res.ok) return {ok: false, status: res.status, error: 'validate status ' + res.status};
                    const data = await res.json();
                    return {ok: true, continue_url: data.continue_url};
                } catch (e) {
                    return {ok: false, error: String(e)};
                }
            }""", otp_code)
            if val_res.get("ok") and val_res.get("continue_url"):
                continue_url = val_res["continue_url"]
        except Exception as eval_exc:
            logger.debug("[Cloak 2FA] 通过 fetch 提交 OTP 抛出异常，尝试 DOM 填入: %s", eval_exc)

        # DOM 备选提交
        if not continue_url:
            for selector in ["input[name='code']", "input[type='text']", "input[autocomplete='one-time-code']"]:
                try:
                    elems = driver.find_elements("css selector", selector)
                    if elems and elems[0].is_displayed():
                        elems[0].clear()
                        elems[0].send_keys(otp_code)
                        break
                except Exception:
                    pass
            for btn_sel in ["button[type='submit']", "button[name='action']", "button:has-text('Continue')", "button:has-text('继续')"]:
                try:
                    btns = driver.find_elements("css selector", btn_sel)
                    if btns and btns[0].is_displayed():
                        btns[0].click()
                        break
                except Exception:
                    pass
        else:
            logger.info("[Cloak 2FA] 正在跟随 continue_url 返回 chatgpt.com...")
            driver.get(continue_url)

        # 4. 等待跳转回 chatgpt.com
        logger.info("[Cloak 2FA] 等待回到 chatgpt.com 会话...")
        start_w = time.time()
        while time.time() - start_w < 40:
            cur_url = str(driver.current_url or "")
            if "chatgpt.com" in cur_url and "auth.openai.com" not in cur_url:
                break
            time.sleep(1)

        time.sleep(2)
        # 5. 获取新鲜 accessToken
        logger.info("[Cloak 2FA] 正在获取重认证后的新鲜 accessToken...")
        new_token = page.evaluate("""async () => {
            try {
                const res = await fetch('/api/auth/session');
                const data = await res.json();
                return data.accessToken || '';
            } catch (e) {
                return '';
            }
        }""")
        if not new_token:
            raise RuntimeError("未获取到新鲜 accessToken")

        # 6. 发起 TOTP enroll
        logger.info("[Cloak 2FA] 正在向 /backend-api/accounts/mfa/enroll 注册 TOTP...")
        enroll_res = page.evaluate("""async (token) => {
            try {
                const res = await fetch('/backend-api/accounts/mfa/enroll', {
                    method: 'POST',
                    headers: {
                        'Authorization': 'Bearer ' + token,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({factor_type: 'totp'})
                });
                if (!res.ok) return {ok: false, status: res.status, error: 'enroll status ' + res.status};
                const data = await res.json();
                return {ok: true, secret: data.secret, session_id: data.session_id};
            } catch (e) {
                return {ok: false, error: String(e)};
            }
        }""", new_token)

        if not enroll_res.get("ok") or not enroll_res.get("secret"):
            raise RuntimeError(f"TOTP enroll 失败: {enroll_res}")

        secret = enroll_res["secret"]
        session_id = enroll_res["session_id"]
        logger.info("[Cloak 2FA] 已获取 TOTP Secret: %s...%s, session_id=%s", secret[:4], secret[-4:], session_id)

        # 7. 本地生成动态码并激活 TOTP
        totp_code = pyotp.TOTP(secret).now()
        logger.info("[Cloak 2FA] 正在激活 TOTP，动态验证码: %s...", totp_code)
        activate_res = page.evaluate("""async (payload) => {
            try {
                const res = await fetch('/backend-api/accounts/mfa/user/activate_enrollment', {
                    method: 'POST',
                    headers: {
                        'Authorization': 'Bearer ' + payload.token,
                        'Content-Type': 'application/json'
                    },
                    body: JSON.stringify({
                        code: payload.code,
                        factor_type: 'totp',
                        session_id: payload.session_id
                    })
                });
                if (!res.ok) return {ok: false, status: res.status, error: 'activate status ' + res.status};
                const data = await res.json();
                return {ok: true, data: data};
            } catch (e) {
                return {ok: false, error: String(e)};
            }
        }""", {"token": new_token, "code": totp_code, "session_id": session_id})

        if not activate_res.get("ok"):
            raise RuntimeError(f"TOTP 激活失败: {activate_res}")

        logger.info("=" * 50)
        logger.info("[Cloak 2FA] ✅ 浏览器内 2FA (TOTP) 设置与激活成功! Secret: %s...%s", secret[:4], secret[-4:])
        logger.info("=" * 50)
        return secret, new_token

    except Exception as exc:
        logger.exception("[Cloak 2FA] 浏览器内 2FA 设置失败（不影响基础注册）：%s", exc)
        return None, None


def _run_cloak_registration_impl(
    email: str | None,
    name: str,
    birthday: str,
    proxy: str = None,
    country: str | None = None,
    otp_code: str = None,
    batch_dir: Path | None = None,
    on_email_acquired: Callable[[str], None] | None = None,
) -> dict:
    """CloakBrowser 自动化注册具体实现。"""
    driver = None
    opened = None
    create_acknowledged = False
    openai_password: str | None = None
    traffic_tracker: PlaywrightTrafficTracker | None = None
    data_saver: BrowserDataSaver | None = None
    network_traffic: dict | None = None
    account_saved = False
    try:
        driver, opened = build_cloak_driver(proxy=proxy, country=country)
        try:
            traffic_tracker = PlaywrightTrafficTracker(driver.context, label="Cloak")
        except Exception as exc:
            # 统计失败不应影响注册主流程。
            logger.warning("[Cloak注册] 初始化浏览器流量统计失败，继续注册：%s: %s", type(exc).__name__, str(exc)[:180])
        data_saver = BrowserDataSaver(label="Cloak")
        if traffic_tracker is not None:
            traffic_tracker.attach_data_saver(data_saver)
        data_saver.install_playwright(driver.context)
        logger.info("[Cloak注册] 开始：%s，profile=%s", email, opened.profile_id)

        otp_after_ts = time.time()
        logger.info("[Cloak注册] 打开登录页：https://chatgpt.com/auth/login")
        _safe_get(
            driver,
            "https://chatgpt.com/auth/login",
            timeout=min(45, int(getattr(_cfg, "CLOAK_SELENIUM_TIMEOUT", 90) or 90)),
            attempts=2,
            accept_hosts=("chatgpt.com", "auth.openai.com"),
        )
        human_delay("navigate")
        _maybe_accept(driver)
        _check_manual_stop()

        # Cloudflare Turnstile / 5 秒盾自动清障与人工接管等待
        is_headless = bool(getattr(_cfg, "CLOAK_HEADLESS", False))
        takeover_timeout = int(getattr(_cfg, "CLOAK_HUMAN_TAKEOVER_TIMEOUT", 30) or 30)
        max_auto_clicks = int(getattr(_cfg, "CLOAK_AUTO_CLEAR_MAX_ATTEMPTS", 2) or 2)

        def _relaunch_headed(target_url: str | None = None):
            nonlocal driver, opened
            logger.info("[Cloak注册] 自动清障未通过，正在切换为有头窗口模式弹出以供人工接管...")
            raw_url = target_url or getattr(driver, "current_url", "") or "https://chatgpt.com/auth/login"
            # 去除 URL 中的 Cloudflare 质询残留参数，避免重开后继续携带质询 token
            if "__cf_chl_" in raw_url:
                curr_url = raw_url.split("?")[0]
            else:
                curr_url = raw_url
            try:
                driver.quit()
            except Exception:
                pass
            driver, opened = build_cloak_driver(proxy=proxy, country=country, headless=False)
            driver._registration_log_prefix = "[Cloak注册]"
            driver._relaunch_headed = _relaunch_headed
            _safe_get(
                driver,
                curr_url,
                timeout=min(45, int(getattr(_cfg, "CLOAK_SELENIUM_TIMEOUT", 90) or 90)),
                attempts=2,
                accept_hosts=("chatgpt.com", "auth.openai.com"),
            )
            human_delay("navigate")
            _maybe_accept(driver)
            return driver

        driver._relaunch_headed = _relaunch_headed
        cf_ok = solve_or_wait_cloudflare_turnstile(
            driver,
            timeout=takeover_timeout,
            max_auto_clicks=max_auto_clicks,
            is_headless=is_headless,
            check_stop_cb=_check_manual_stop,
            on_relaunch_headed=_relaunch_headed,
        )
        if not cf_ok:
            raise VerificationTimeoutError(f"Cloudflare 人机验证等待超时（{takeover_timeout}秒未解决），标记并跳过此账号")
        _check_manual_stop()

        def _email_supplier_after_input() -> str:
            nonlocal email
            _check_manual_stop()
            email = acquire_email_after_input(email)
            if on_email_acquired:
                on_email_acquired(email)
            return email

        next_state = _submit_email_and_wait_next(
            driver,
            email,
            attempts=3,
            email_supplier=_email_supplier_after_input,
        )
        _check_manual_stop()

        # 如果邮箱提交后直接进入验证码页，也尝试点击“使用密码继续”进入密码创建页；
        # _fill_password_page_if_present 会在设置成功后返回本次 OpenAI 注册密码。
        openai_password = _fill_password_page_if_present(driver, email, timeout=25)
        _check_manual_stop()

        current_otp = otp_code
        max_otp_attempts = 3
        for otp_attempt in range(1, max_otp_attempts + 1):
            if current_otp is None:
                logger.info("[Cloak注册][OTP] 等待验证码：%s（第 %s/%s 次）", email, otp_attempt, max_otp_attempts)
                try:
                    current_otp = wait_for_otp(email, after_ts=otp_after_ts)
                except Exception as exc:
                    if otp_attempt >= max_otp_attempts:
                        raise
                    logger.warning(
                        "[Cloak注册][OTP] 一直未收到验证码，点击“重新发送电子邮件”后继续等待（下一轮 %s/%s）：%s: %s",
                        otp_attempt + 1,
                        max_otp_attempts,
                        type(exc).__name__,
                        str(exc)[:180],
                    )
                    # otp_after_ts = time.time()  # 不重置时间戳，以便接收第一轮因网络延迟姗姗来迟的验证码
                    _click_resend_email_otp(driver, timeout=25)
                    human_delay("api")
                    current_otp = None
                    continue
            logger.info("[Cloak注册][OTP] 收到验证码：%s", current_otp)
            _clear_otp_inputs(driver)
            _type_otp(driver, current_otp)
            human_delay("otp_input")
            try:
                _click_continue(driver)
            except Exception as exc:
                logger.info("[Cloak注册][OTP] 未找到显式提交按钮，继续等待页面状态：%s", str(exc)[:120])

            outcome = _wait_after_email_otp_submit(driver, timeout=10)
            if outcome == "accepted":
                break
            if otp_attempt >= max_otp_attempts:
                raise RuntimeError("邮箱验证码连续错误/过期，已达到最大重试次数")
            # otp_after_ts = time.time()  # 不重置时间戳，防止漏掉延迟邮件
            _click_resend_email_otp(driver, timeout=25)
            human_delay("api")
            current_otp = None

        profile_submitted = _complete_profile_page(driver, name, birthday, timeout=60)
        if profile_submitted:
            create_acknowledged = True
            human_delay("post_auth")

        session_info = _fetch_chatgpt_session(driver, timeout=120)
        access_token = session_info["accessToken"]
        logger.info("[Cloak注册] 已拿到 accessToken：%s", email)

        totp_secret = None
        if bool(getattr(_twofa_cfg, "ENABLE_2FA", False)):
            try:
                secret, fresh_token = setup_cloak_inbrowser_2fa(driver, email)
                if secret:
                    totp_secret = secret
                if fresh_token:
                    access_token = fresh_token
            except Exception as twofa_exc:
                logger.warning("[Cloak注册] 2FA 设置异常（不影响注册完成）：%s", twofa_exc)

        codex_result = {
            "status": "skipped",
            "ok": True,
            "message": "ENABLE_CODEX_AUTO=False，跳过 Codex",
        }
        try:
            from config import codex as _codex_cfg
            if bool(getattr(_codex_cfg, "ENABLE_CODEX_AUTO", False)):
                from core.roxy_codex_oauth import run_roxy_codex_oauth
                logger.info("[Cloak注册][Codex] ENABLE_CODEX_AUTO=True，复用当前 CloakBrowser 窗口执行 Codex 授权")
                _check_manual_stop()
                codex_result = run_roxy_codex_oauth(
                    email,
                    reuse_existing_profile=True,
                    existing_driver=driver,
                    existing_opened=opened,
                    force=True,
                    clear_existing_state=True,
                )
            else:
                logger.info("[Cloak注册][Codex] ENABLE_CODEX_AUTO=False，注册后跳过 Codex OAuth")
        except Exception as exc:
            codex_result = {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {str(exc)[:180]}"}

        # 提取真实出口代理与地区信息
        actual_proxy = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
        geo_info = ((opened.raw or {}).get("locale") or {}).get("geo") or {}
        actual_country = geo_info.get("country") or country

        # 先持久化账号数据并触发同步，确保账号安全存盘
        account_id = save_account_data(
            email=email,
            access_token=access_token,
            totp_secret=totp_secret,
            country=actual_country,
            email_source=resolve_email_source(email),
            proxy_used=actual_proxy,
            batch_dir=batch_dir,
            extra={
                "user": session_info.get("user"),
                "account": session_info.get("account"),
                "expires": session_info.get("expires"),
                "cloakbrowser": {"profile_id": opened.profile_id, "open_result": opened.raw},
                "registration_password": openai_password,
                "codex": codex_result,
            },
        )
        account_saved = True
        # 记录 IP 配额计数 (+1)
        try:
            from core.ip_quota_manager import ip_quota_manager
            ip_quota_manager.record_account_success(
                proxy_url=actual_proxy,
                country=actual_country,
                email=email,
            )
        except Exception as q_exc:
            logger.warning("[Cloak注册] 记录 IP 配额失败：%s", q_exc)

        # 统计注册浏览器关闭前的完整会话与停留
        try:
            post_register_dwell(email, label="Cloak注册")
        except Exception as d_exc:
            logger.warning("[Cloak注册] 注册后停留异常：%s", d_exc)

        try:
            if traffic_tracker is not None:
                network_traffic = traffic_tracker.stop()
            if data_saver is not None:
                data_saver.stop()
        except Exception as t_exc:
            logger.warning("[Cloak注册] 流量统计停止异常：%s", t_exc)

        codex_ok = codex_result.get("ok") or codex_result.get("status") == "skipped"
        return {
            "success": bool(codex_ok),
            "email": email,
            "account_id": account_id,
            "access_token": access_token,
            "totp_secret": totp_secret,
            "codex": codex_result,
            "network_traffic": network_traffic,
            "error": None if codex_ok else f"Codex 未完成: {codex_result.get('message')}",
        }
    except Exception as exc:
        if traffic_tracker is not None:
            try:
                network_traffic = traffic_tracker.stop()
            except Exception:
                pass
        if data_saver is not None:
            data_saver.stop()
        logger.error("[Cloak注册] 失败：%s: %s", type(exc).__name__, exc)
        logger.debug("[Cloak注册] 失败详情", exc_info=True)
        try:
            actual_p = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
            if not account_saved and actual_p:
                from core.ip_quota_manager import ip_quota_manager
                exc_str = str(exc)
                is_net_err = any(sig in exc_str for sig in (
                    "ERR_TUNNEL_CONNECTION_FAILED", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET",
                    "ERR_PROXY_CONNECTION_FAILED", "ERR_NAME_NOT_RESOLVED", "net::", "Timeout 45000ms exceeded"
                )) or isinstance(exc, (TimeoutError,))
                if is_net_err:
                    ip_quota_manager.record_proxy_failure(actual_p, reason=type(exc).__name__, country=country)
                else:
                    ip_quota_manager.release_in_flight(actual_p)
        except Exception:
            pass
        try:
            if email:
                from core.email_provider import release_email
                from core.openai_auth import detect_account_unusable_text
                dead_code = detect_account_unusable_text(str(exc))
                if dead_code:
                    logger.warning("[Cloak注册][封禁剔除] 邮箱 %s 检测到已被 OpenAI 停用/封禁 (%s)，标记为已封禁并放弃注册", email, dead_code)
                    release_email(email, status="banned", note=f"GPT已封禁: {dead_code}")
                elif type(exc).__name__ == 'AlreadyRegisteredError':
                    logger.warning("[Cloak注册] 邮箱 %s 已被注册或验证，标记为需要重新登录", email)
                    release_email(email, status="relogin", note="已注册，需重新登录获取 Session")
                elif isinstance(exc, VerificationTimeoutError) or "人机验证" in str(exc) or "Cloudflare" in str(exc):
                    logger.warning("[Cloak注册] 人机验证超时未解决，标记邮箱为失败并跳过该账号：%s", email)
                    release_email(email, status="failed", note="人机验证超时未解决")
                else:
                    release_email(email, status="failed" if create_acknowledged else "available", note=f"Cloak注册失败: {str(exc)[:180]}")
        except Exception:
            pass
        return {
            "success": False,
            "email": email,
            "network_traffic": network_traffic,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
            "skipped": True if isinstance(exc, VerificationTimeoutError) else False,
        }
    finally:
        if traffic_tracker is not None:
            try:
                traffic_tracker.stop()
            except Exception:
                pass
        if data_saver is not None:
            data_saver.stop()
        if driver and not bool(_cfg.CLOAK_KEEP_BROWSER_OPEN):
            try:
                driver.quit()
            except Exception:
                pass


def run_cloak_registration(*args, **kwargs) -> dict:
    """CloakBrowser 自动化注册入口（在独立隔离线程中执行，彻底杜绝线程池复用导致的 Playwright Sync API 冲突）。"""
    import threading
    result_box = {}
    error_box = {}
    parent_thread_name = threading.current_thread().name

    def _target():
        try:
            result_box["value"] = _run_cloak_registration_impl(*args, **kwargs)
        except BaseException as exc:  # noqa: BLE001
            error_box["error"] = exc

    t = threading.Thread(target=_target, name=parent_thread_name, daemon=True)
    t.start()
    while t.is_alive():
        t.join(timeout=0.8)
        try:
            _check_manual_stop()
        except Exception:
            raise
    if "error" in error_box:
        raise error_box["error"]
    return result_box.get("value") or {}
