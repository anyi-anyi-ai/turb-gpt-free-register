# -*- coding: utf-8 -*-
"""通过 RoxyBrowser 指纹浏览器 + Selenium 执行 ChatGPT 注册。"""
from __future__ import annotations

import logging
import random
import string
import time
import uuid
from pathlib import Path
from typing import Callable

from config import roxybrowser as _cfg
from config import twofa as _twofa_cfg
from core.account_export import save_account_data, post_register_dwell
from core.browser_data_saver import BrowserDataSaver
from core.browser_traffic import SeleniumTrafficTracker
from core.email_provider import acquire_email_after_input, wait_for_otp, resolve_email_source
from core.humanize import delay as human_delay
from core.roxybrowser_client import RoxyBrowserClient, RoxyOpenResult
from core.white_screen_guard import WhiteScreenError, is_white_screen, wait_with_white_screen_guard

# 引入并重新导出认证流水线各阶段函数，保持 100% 向后兼容
from core.auth_flow import *

logger = logging.getLogger(__name__)


def _enable_performance_logging(options) -> None:
    """尽量开启 Chrome performance log；不支持时不阻断注册。"""
    try:
        options.set_capability("goog:loggingPrefs", {"performance": "ALL"})
    except Exception as exc:
        logger.debug("[Roxy] 当前 Selenium 选项不支持 performance log：%s", exc)



def _build_driver(opened: RoxyOpenResult):
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.chrome.service import Service
    from selenium.webdriver.remote.webdriver import WebDriver as RemoteWebDriver

    if opened.debugger_address:
        logger.info("[Roxy] Selenium 连接 debuggerAddress=%s", opened.debugger_address)
        options = Options()
        # 页面里长轮询/风控脚本偶尔会让 driver.get 等到超时；eager 只等 DOMContentLoaded。
        options.page_load_strategy = "eager"
        _enable_performance_logging(options)
        options.add_experimental_option("debuggerAddress", opened.debugger_address)
        driver_path = ""
        try:
            raw_data = opened.raw.get("data") if isinstance(opened.raw, dict) else {}
            if isinstance(raw_data, dict):
                driver_path = str(raw_data.get("driver") or raw_data.get("driverPath") or raw_data.get("driver_path") or "").strip()
        except Exception:
            driver_path = ""
        if driver_path:
            logger.info("[Roxy] 使用 Roxy chromedriver=%s", driver_path)
            driver = webdriver.Chrome(service=Service(executable_path=driver_path), options=options)
        else:
            driver = webdriver.Chrome(options=options)
        _apply_browser_automation_mask(driver)
        return driver

    if opened.webdriver_url:
        logger.info("[Roxy] Selenium 连接 webdriver_url=%s", opened.webdriver_url)
        options = Options()
        options.page_load_strategy = "eager"
        _enable_performance_logging(options)
        driver = RemoteWebDriver(command_executor=opened.webdriver_url, options=options)
        _apply_browser_automation_mask(driver)
        return driver

    raise RuntimeError("Roxy 未返回可连接的 Selenium 地址")


def _center_browser_window(driver) -> None:
    """把可见的 Roxy 窗口移动到 Windows 主屏工作区中央。"""
    if bool(getattr(_cfg, "ROXY_OPEN_HEADLESS", False)):
        return
    try:
        import platform
        if platform.system().lower() != "windows":
            return
        import ctypes

        class _Rect(ctypes.Structure):
            _fields_ = [
                ("left", ctypes.c_long),
                ("top", ctypes.c_long),
                ("right", ctypes.c_long),
                ("bottom", ctypes.c_long),
            ]

        work_area = _Rect()
        if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(work_area), 0):
            raise OSError("无法读取 Windows 工作区")
        size = driver.get_window_size()
        width = max(1, int(size.get("width") or 1))
        height = max(1, int(size.get("height") or 1))
        x = int(work_area.left + max(0, (work_area.right - work_area.left - width) // 2))
        y = int(work_area.top + max(0, (work_area.bottom - work_area.top - height) // 2))
        driver.set_window_position(x, y)
        logger.info("[Roxy] 浏览器窗口已居中：x=%s y=%s width=%s height=%s", x, y, width, height)
    except Exception as exc:
        logger.warning("[Roxy] 浏览器窗口居中失败，继续执行：%s", exc)

def _try_salvage_by_login(
    driver,
    email: str,
    opened: RoxyOpenResult,
    proxy: str | None = None,
    batch_dir=None,
    traffic_tracker=None,
) -> dict | None:
    """智能转登录预案：针对已在 GPT 注册过的账号，尝试走登录/邮箱一次性验证码流程完成认证并入库。"""
    try:
        logger.info("[Roxy注册][转登录预案] 邮箱 %s 触发转登录，正在导航至登录入口：https://chatgpt.com/auth/login", email)
        _safe_get(driver, "https://chatgpt.com/auth/login")
        time.sleep(2.0)
        _maybe_accept(driver)

        # 1. 查找并填写邮箱
        email_input = _wait_for_email_input(driver, timeout=20)
        _human_type_text(driver, email_input, email, clear=True)
        human_delay("form")
        _submit_email_step(driver, email)
        logger.info("[Roxy注册][转登录预案] 已提交邮箱，等待登录响应")

        # 2. 等待进入验证码页或密码页
        login_state = _wait_email_submit_next_state(driver, email, timeout=25)
        if login_state == "login_password":
            logger.info("[Roxy注册][转登录预案] 当前处于密码页，尝试切换至一次性验证码登录 (Passwordless)...")
            res = _click_passwordless_signup_if_present(driver)
            if res.get("ok"):
                logger.info("[Roxy注册][转登录预案] 成功点击一次性验证码登录按钮，等待进入 OTP 页")
                time.sleep(2.0)
                login_state = _detect_current_auth_stage(driver)
            else:
                logger.warning("[Roxy注册][转登录预案] 密码页未找到一次性验证码入口，无法继续自动登录")
                return None

        # 3. 填入验证码
        if login_state == "otp" or _is_email_verification_page(driver):
            logger.info("[Roxy注册][转登录预案] 进入 OTP 验证码页，等待接收登录验证码...")
            otp_code = None
            otp_after = time.time() - 30.0
            for attempt in range(1, 4):
                try:
                    otp_code = wait_for_otp(email, after_ts=otp_after, max_wait=35)
                    break
                except Exception as otp_err:
                    logger.warning("[Roxy注册][转登录预案] 获取验证码重试中 (%s/3): %s", attempt, otp_err)
                    _click_resend_email_otp(driver, timeout=15)
                    time.sleep(3.0)
            if not otp_code:
                logger.warning("[Roxy注册][转登录预案] 未能获取到登录验证码，转登录放弃")
                return None

            logger.info("[Roxy注册][转登录预案] 收到登录验证码：%s，开始输入", otp_code)
            _clear_otp_inputs(driver)
            _type_otp(driver, otp_code)
            human_delay("otp_input")
            try:
                _click_continue(driver)
            except Exception:
                pass
            outcome = _wait_after_email_otp_submit(driver, timeout=30)
            logger.info("[Roxy注册][转登录预案] OTP 提交结果: %s", outcome)

        # 4. 获取登录态
        time.sleep(2.0)
        _check_manual_stop()
        logger.info("[Roxy注册][转登录预案] 正在提取登录会话 Session/Token...")
        session_info = _fetch_chatgpt_session(driver, timeout=90)
        access_token = session_info["accessToken"]
        logger.info("[Roxy注册][转登录预案] 成功提取到 accessToken！账号已挽救：%s", email)

        captured_storage = None
        try:
            cookies = driver.get_cookies() if hasattr(driver, "get_cookies") else []
            local_storage = driver.execute_script("return {...localStorage};") if hasattr(driver, "execute_script") else {}
            if cookies or local_storage:
                origins = []
                if local_storage:
                    origins.append({
                        "origin": "https://chatgpt.com",
                        "localStorage": [{"name": str(k), "value": str(v)} for k, v in local_storage.items()]
                    })
                captured_storage = {"cookies": cookies or [], "origins": origins}
        except Exception:
            pass

        network_traffic = None
        if traffic_tracker is not None:
            try:
                network_traffic = traffic_tracker.stop()
            except Exception:
                pass

        account_id = save_account_data(
            email=email,
            access_token=access_token,
            totp_secret=None,
            email_source=resolve_email_source(email),
            proxy_used=proxy or None,
            batch_dir=batch_dir,
            storage_state=captured_storage,
            extra={
                "user": session_info.get("user"),
                "account": session_info.get("account"),
                "expires": session_info.get("expires"),
                "roxybrowser": {"profile_id": opened.profile_id, "open_result": opened.raw},
                "salvaged_from": "user_already_exists",
                "network_traffic": network_traffic,
            },
        )
        logger.info("[Roxy注册][预案大功告成] 已存在账号成功转登录入库！email=%s account_id=%s", email, account_id)
        return {
            "success": True,
            "email": email,
            "account_id": account_id,
            "access_token": access_token,
            "totp_secret": None,
            "codex": {"status": "skipped", "ok": True, "message": "已存在账号转登录入库"},
            "network_traffic": network_traffic,
            "error": None,
        }
    except Exception as e:
        logger.warning("[Roxy注册][转登录预案异常] 转登录未能完成: %s: %s", type(e).__name__, e)
        return None


def run_roxy_registration(
    email: str | None,
    name: str,
    birthday: str,
    proxy: str = None,
    otp_code: str = None,
    batch_dir: Path | None = None,
    on_email_acquired: Callable[[str], None] | None = None,
) -> dict:
    """Roxy 指纹浏览器自动化注册入口。"""
    client = RoxyBrowserClient()
    opened = client.open_profile()
    driver = None
    create_acknowledged = False
    openai_password: str | None = None
    traffic_tracker: SeleniumTrafficTracker | None = None
    data_saver: BrowserDataSaver | None = None
    network_traffic: dict | None = None

    def _traffic_checkpoint() -> None:
        if traffic_tracker is not None:
            try:
                traffic_tracker.checkpoint()
            except Exception as exc:
                logger.debug("[Roxy注册] 刷新浏览器流量统计失败：%s", exc)

    try:
        driver = _build_driver(opened)
        try:
            traffic_tracker = SeleniumTrafficTracker(driver, label="Roxy")
        except Exception as exc:
            # 统计失败不应影响注册主流程。
            logger.warning("[Roxy注册] 初始化浏览器流量统计失败，继续注册：%s: %s", type(exc).__name__, str(exc)[:180])
        data_saver = BrowserDataSaver(label="Roxy")
        if traffic_tracker is not None:
            traffic_tracker.attach_data_saver(data_saver)
        data_saver.install_selenium(driver)
        _center_browser_window(driver)
        driver.set_page_load_timeout(int(_cfg.ROXY_SELENIUM_TIMEOUT))
        try:
            driver.set_script_timeout(12)
        except Exception:
            pass
        logger.info("[Roxy注册] 开始：%s，profile=%s", email, opened.profile_id)

        otp_after_ts = time.time()
        logger.info("[Roxy注册] 打开登录页：https://chatgpt.com/auth/login")
        _safe_get(
            driver,
            "https://chatgpt.com/auth/login",
            timeout=min(45, int(getattr(_cfg, "ROXY_SELENIUM_TIMEOUT", 90) or 90)),
            attempts=2,
            accept_hosts=("chatgpt.com", "auth.openai.com"),
        )
        _traffic_checkpoint()
        human_delay("navigate")
        _page_warmup(driver, reason="login_page")
        logger.info("[Roxy注册] 登录页加载完成，准备填写邮箱")
        _maybe_accept(driver)
        _check_manual_stop()

        # 填邮箱。OpenAI UI 会随出口 IP/语言变化；这里只按 DOM 技术属性找邮箱入口，
        # 并排除 Google/Apple/Microsoft 等第三方入口，不依赖按钮可见文字。
        def _email_supplier_after_input() -> str:
            nonlocal email
            _check_manual_stop()
            email = acquire_email_after_input(email)
            if on_email_acquired:
                on_email_acquired(email)
            return email

        try:
            next_state = _submit_email_and_wait_next(
                driver,
                email,
                attempts=3,
                email_supplier=_email_supplier_after_input,
            )
        except (AlreadyRegisteredError, UserAlreadyExistsError) as exist_exc:
            if bool(getattr(_cfg, "ENABLE_LOGIN_FALLBACK_ON_EXISTING", True)):
                logger.warning(
                    "[Roxy注册][预案触发] 邮箱 %s 已在 GPT 注册过 (%s)，尝试执行智能转登录挽救...",
                    email, exist_exc
                )
                salvaged = _try_salvage_by_login(
                    driver=driver,
                    email=email,
                    opened=opened,
                    proxy=proxy,
                    batch_dir=batch_dir,
                    traffic_tracker=traffic_tracker,
                )
                if salvaged:
                    return salvaged
            raise
        _traffic_checkpoint()
        _check_manual_stop()

        # 新版注册流如果邮箱后直接进入验证码页，也优先点击“使用密码继续”进入
        # /create-account/password，设置密码并把密码写入账号 extra.registration_password。
        openai_password = _fill_password_page_if_present(driver, email, timeout=25)
        _traffic_checkpoint()
        _check_manual_stop()

        current_otp = otp_code
        max_otp_attempts = 3
        for otp_attempt in range(1, max_otp_attempts + 1):
            if current_otp is None:
                logger.info("[Roxy注册][OTP] 等待验证码：%s（第 %s/%s 次）", email, otp_attempt, max_otp_attempts)
                try:
                    current_otp = wait_for_otp(email, after_ts=otp_after_ts)
                except Exception as exc:
                    if otp_attempt >= max_otp_attempts:
                        raise
                    # 兜底：OpenAI 重发验证码时常常是同一封邮件（时间戳不变），
                    # after_ts 过滤会把它当成旧邮件忽略。先宽松取最新一条验证码，
                    # 取到就直接用它重试提交，避免误点“重新发送”后死等。
                    fallback_otp = None
                    try:
                        fallback_otp = wait_for_otp(email, after_ts=0.0, max_wait=15, poll_interval=3)
                    except Exception:
                        fallback_otp = None
                    if fallback_otp:
                        logger.info(
                            "[Roxy注册][OTP] 取码接口超时但宽松取到最新验证码，直接重试提交：%s (fallback)",
                            fallback_otp,
                        )
                        current_otp = fallback_otp
                        continue
                    logger.warning(
                        "[Roxy注册][OTP] 一直未收到验证码，点击“重新发送电子邮件”后继续等待（下一轮 %s/%s）：%s: %s",
                        otp_attempt + 1,
                        max_otp_attempts,
                        type(exc).__name__,
                        str(exc)[:180],
                    )
                    # otp_after_ts = time.time()
                    _click_resend_email_otp(driver, timeout=25)
                    human_delay("api")
                    current_otp = None
                    continue
            logger.info("[Roxy注册][OTP] 收到验证码：%s", current_otp)
            if _is_signup_password_page(driver):
                logger.info("[Roxy注册][OTP] 检测到当前页面处于密码设置页，先行自动设置密码...")
                pwd = _fill_password_page_if_present(driver, email, timeout=15)
                if pwd:
                    openai_password = pwd
                if _is_signup_password_page(driver):
                    try:
                        _click_continue(driver)
                    except Exception:
                        pass
                    time.sleep(2.0)
            if not _is_signup_password_page(driver):
                _clear_otp_inputs(driver)
                _type_otp(driver, current_otp)
                logger.info("[Roxy注册][OTP] 已填写邮箱验证码")
                _check_manual_stop()
                human_delay("otp_input")
                try:
                    _click_continue(driver)
                    logger.info("[Roxy注册][OTP] 已提交邮箱验证码，等待资料页或登录态")
                except Exception as exc:
                    logger.info("[Roxy注册][OTP] 未找到显式提交按钮，继续等待页面状态：%s", str(exc)[:120])

            outcome = _wait_after_email_otp_submit(driver, timeout=30)
            _traffic_checkpoint()
            if outcome == 'accepted':
                break
            if otp_attempt >= max_otp_attempts:
                raise RuntimeError("邮箱验证码连续错误/过期，已达到最大重试次数")
            logger.warning("[Roxy注册][OTP] 验证码错误/过期，准备重新发送并重新获取验证码（%s/%s）", otp_attempt + 1, max_otp_attempts)
            # otp_after_ts = time.time()
            _click_resend_email_otp(driver, timeout=25)
            _traffic_checkpoint()
            human_delay("api")
            current_otp = None

        # about-you / profile 信息页：必须完成或确认已有登录态，不能静默跳过。
        logger.info("[Roxy注册] 开始等待资料页/登录态")
        try:
            profile_submitted = _complete_profile_page(driver, name, birthday, timeout=60)
        except (AlreadyRegisteredError, UserAlreadyExistsError) as exist_exc:
            if bool(getattr(_cfg, "ENABLE_LOGIN_FALLBACK_ON_EXISTING", True)):
                logger.warning(
                    "[Roxy注册][预案触发] 资料页检测到邮箱 %s 已在 GPT 注册过 (%s)，尝试执行智能转登录挽救...",
                    email, exist_exc
                )
                salvaged = _try_salvage_by_login(
                    driver=driver,
                    email=email,
                    opened=opened,
                    proxy=proxy,
                    batch_dir=batch_dir,
                    traffic_tracker=traffic_tracker,
                )
                if salvaged:
                    return salvaged
            raise
        _traffic_checkpoint()
        if profile_submitted:
            create_acknowledged = True
            # 给 OAuth 回调 / session cookie 写入一点时间。
            human_delay("post_auth")

        logger.info("[Roxy注册] 等待 ChatGPT 跳转并写入 session/accessToken")
        _check_manual_stop()
        session_info = _fetch_chatgpt_session(driver, timeout=120)
        _traffic_checkpoint()
        access_token = session_info["accessToken"]
        logger.info("[Roxy注册] 已拿到 accessToken：%s", email)
        _check_manual_stop()

        if _twofa_cfg.ENABLE_2FA:
            logger.warning("[Roxy注册] 当前 Roxy 自动化路径暂不执行 2FA 设置，已跳过")
        totp_secret = None

        codex_result = {
            "status": "skipped",
            "ok": True,
            "message": "ENABLE_CODEX_AUTO=False，跳过 Codex",
        }
        try:
            from config import codex as _codex_cfg
            if bool(getattr(_codex_cfg, "ENABLE_CODEX_AUTO", False)):
                # 注册流程本身已创建 Roxy 一号一环境。这里不能再新建第二个 Roxy 环境；
                # 复用当前注册窗口，先清理 Cookie/session/localStorage/cache，再开始 Codex 授权。
                from core.roxy_codex_oauth import run_roxy_codex_oauth
                logger.info("[Roxy注册][Codex] ENABLE_CODEX_AUTO=True，复用当前注册 Roxy 窗口执行 Codex 授权，不创建新环境")
                _check_manual_stop()
                codex_result = run_roxy_codex_oauth(
                    email,
                    reuse_existing_profile=True,
                    existing_driver=driver,
                    existing_opened=opened,
                    force=True,
                    clear_existing_state=True,
                )
                _traffic_checkpoint()
            else:
                logger.info("[Roxy注册][Codex] ENABLE_CODEX_AUTO=False，注册后跳过 Codex OAuth")
        except Exception as exc:
            codex_result = {"status": "failed", "ok": False, "message": f"{type(exc).__name__}: {str(exc)[:180]}"}

        # 统计注册浏览器关闭前的完整会话；注册后停留期间的网络请求也计入。
        post_register_dwell(email, label="Roxy注册")
        _traffic_checkpoint()
        if traffic_tracker is not None:
            network_traffic = traffic_tracker.stop()
        # 导出免密登录态 StorageState (Cookies + LocalStorage)
        captured_storage = None
        try:
            cookies = driver.get_cookies() if hasattr(driver, "get_cookies") else []
            local_storage = driver.execute_script("return {...localStorage};") if hasattr(driver, "execute_script") else {}
            if cookies or local_storage:
                origins = []
                if local_storage:
                    origins.append({
                        "origin": "https://chatgpt.com",
                        "localStorage": [{"name": str(k), "value": str(v)} for k, v in local_storage.items()]
                    })
                captured_storage = {"cookies": cookies or [], "origins": origins}
        except Exception as st_err:
            logger.debug("[Roxy注册] 导出 storage_state 失败: %s", st_err)

        account_id = save_account_data(
            email=email,
            access_token=access_token,
            totp_secret=totp_secret,
            email_source=resolve_email_source(email),
            proxy_used=proxy or None,
            batch_dir=batch_dir,
            storage_state=captured_storage,
            extra={
                "user": session_info.get("user"),
                "account": session_info.get("account"),
                "expires": session_info.get("expires"),
                "roxybrowser": {"profile_id": opened.profile_id, "open_result": opened.raw},
                "registration_password": openai_password,
                "codex": codex_result,
                "network_traffic": network_traffic,
            },
        )
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
        logger.error("[Roxy注册] 失败：%s: %s", type(exc).__name__, exc)
        logger.debug("[Roxy注册] 失败详情", exc_info=True)
        try:
            actual_p = ((opened.raw or {}).get("proxy") if opened else None) or proxy or None
            if actual_p:
                from core.ip_quota_manager import ip_quota_manager
                exc_str = str(exc)
                is_net_err = any(sig in exc_str for sig in (
                    "ERR_TUNNEL_CONNECTION_FAILED", "ERR_CONNECTION_CLOSED", "ERR_CONNECTION_RESET",
                    "ERR_PROXY_CONNECTION_FAILED", "ERR_NAME_NOT_RESOLVED", "net::", "Timeout 45000ms exceeded"
                )) or isinstance(exc, (TimeoutError,)) or "WhiteScreenError" in exc_str
                if is_net_err:
                    ip_quota_manager.record_proxy_failure(actual_p, reason=type(exc).__name__, country=country)
                else:
                    ip_quota_manager.release_in_flight(actual_p)
        except Exception:
            pass
        # 未确认创建前回收邮箱；确认后避免重复使用。
        try:
            if email:
                from core.email_provider import release_email
                from core.white_screen_guard import WhiteScreenError
                from core.openai_auth import detect_account_unusable_text
                dead_code = detect_account_unusable_text(str(exc))
                if dead_code:
                    logger.warning("[Roxy注册][封禁剔除] 邮箱 %s 检测到已被 OpenAI 停用/封禁 (%s)，标记为已封禁并放弃注册", email, dead_code)
                    release_email(email, status="banned", note=f"GPT已封禁: {dead_code}")
                elif type(exc).__name__ in ('AlreadyRegisteredError', 'UserAlreadyExistsError') or "user_already_exists" in str(exc).lower() or "AlreadyRegisteredError" in str(exc):
                    logger.warning("[Roxy注册] 邮箱 %s 检测到已在 GPT 注册过 (user_already_exists)，标记为停用并隔离", email)
                    release_email(email, status="disabled", note="已注册过GPT: user_already_exists")
                elif isinstance(exc, WhiteScreenError) or "WhiteScreenError" in str(exc):
                    logger.warning("[Roxy注册][白屏失效] 页面持续白屏自愈失败，代理 %s 已淘汰换新，将邮箱 %s 释放回可用状态以供重试", actual_p, email)
                    release_email(email, status="available", note="白屏自愈失败，释放邮箱重试")
                else:
                    release_email(email, status="failed" if create_acknowledged else "available", note=f"Roxy注册失败: {str(exc)[:180]}")
        except Exception:
            pass
        return {
            "success": False,
            "email": email,
            "network_traffic": network_traffic,
            "error": f"{type(exc).__name__}: {str(exc)[:300]}",
        }
    finally:
        if traffic_tracker is not None:
            try:
                traffic_tracker.stop()
            except Exception:
                pass
        if data_saver is not None:
            data_saver.stop()
        if driver and not bool(_cfg.ROXY_KEEP_BROWSER_OPEN):
            try:
                driver.quit()
            except Exception:
                pass
        if not bool(_cfg.ROXY_KEEP_BROWSER_OPEN):
            client.cleanup_profile(opened)


import sys
from types import ModuleType
from core import auth_flow

class _RoxyModule(ModuleType):
    def __setattr__(self, name: str, value):
        super().__setattr__(name, value)
        if hasattr(self, "_initialized"):
            if hasattr(auth_flow, name) or name.startswith("_"):
                setattr(auth_flow, name, value)
                for submod in getattr(auth_flow, "_SUBMODULES", ()):
                    if hasattr(submod, name):
                        setattr(submod, name, value)

    def __delattr__(self, name: str):
        super().__delattr__(name)
        if hasattr(auth_flow, name):
            try:
                delattr(auth_flow, name)
            except AttributeError:
                pass
            for submod in getattr(auth_flow, "_SUBMODULES", ()):
                if hasattr(submod, name):
                    try:
                        delattr(submod, name)
                    except AttributeError:
                        pass

_curr = sys.modules[__name__]
_self = _RoxyModule(__name__, __doc__)
for _k, _v in list(vars(_curr).items()):
    if not _k.startswith("__"):
        super(_RoxyModule, _self).__setattr__(_k, _v)
_self._initialized = True
sys.modules[__name__] = _self


