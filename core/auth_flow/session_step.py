# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import logging
import time
from typing import Callable

from .human_actions import _log_prefix, _safe_get
from .stages import _check_manual_stop

logger = logging.getLogger(__name__)


def _has_access_token(driver) -> bool:
    try:
        result = driver.execute_async_script(r"""
        const done = arguments[0];
        fetch('https://chatgpt.com/api/auth/session', {credentials:'include'})
          .then(r => r.json()).then(j => done(Boolean(j && j.accessToken)))
          .catch(() => done(false));
        """)
        return bool(result)
    except Exception:
        return False


def _read_chatgpt_session_once(driver) -> dict | None:
    """当前页面必须在 chatgpt.com；读取 /api/auth/session，拿不到 token 返回 None。"""
    script = r"""
    const done = arguments[0];
    fetch('/api/auth/session', {credentials: 'include'})
      .then(r => r.json())
      .then(j => done({ok: true, data: j}))
      .catch(e => done({ok: false, error: String(e)}));
    """
    result = driver.execute_async_script(script)
    if result and result.get("ok"):
        data = result.get("data") or {}
        if data.get("accessToken"):
            logger.info("%s /api/auth/session 已返回 accessToken", _log_prefix(driver))
            return data
        logger.info("%s 等待 ChatGPT session 写入 accessToken，当前响应 keys=%s", _log_prefix(driver), list(data.keys()))
    return None


def _switch_to_chatgpt_window_if_any(driver) -> bool:
    """有些浏览器/适配层会在新窗口完成 callback；尝试切到已有 chatgpt.com 句柄。"""
    try:
        handles = list(getattr(driver, "window_handles", []) or [])
        current_handle = None
        try:
            current_handle = getattr(driver, "current_window_handle", None)
        except Exception:
            current_handle = None
        for handle in handles:
            try:
                driver.switch_to.window(handle)
                if "chatgpt.com" in str(getattr(driver, "current_url", "") or ""):
                    return True
            except Exception:
                continue
        if current_handle is not None:
            try:
                driver.switch_to.window(current_handle)
            except Exception:
                pass
    except Exception:
        pass
    return False


def _fetch_chatgpt_session(driver, timeout: int = 120, auto_jump_wait: int = 45) -> dict:
    """等待页面完成跳转并从 ChatGPT 页面内读取登录 session/accessToken。

    绝不能在 auth.openai.com 正在提交或处理 OAuth 重定向时强行跳转 chatgpt.com/，
    否则会导致 OAuth 授权码丢弃，使 ChatGPT 变成未登录访客页。
    """
    end = time.time() + timeout
    auto_jump_end = time.time() + max(10, int(auto_jump_wait or 45))
    last_data = None
    forced_chatgpt_open = False

    while time.time() < end:
        try:
            current = str(driver.current_url or '')
        except Exception:
            current = ''

        if 'chatgpt.com' not in current:
            if _switch_to_chatgpt_window_if_any(driver):
                current = str(getattr(driver, "current_url", "") or "")
            elif "about-you" in current or "auth.openai.com" in current:
                # 依然在 OpenAI 授权与资料提交处理流中，绝不能强行跨站打开 chatgpt.com
                time.sleep(1.5)
                continue
            elif time.time() >= auto_jump_end and not forced_chatgpt_open:
                try:
                    logger.info("%s 未在 %ss 内观察到当前窗口跳转 chatgpt.com，主动打开 ChatGPT 内读取 session", _log_prefix(driver), int(auto_jump_wait or 45))
                    _safe_get(driver, "https://chatgpt.com/", timeout=35, attempts=2, accept_hosts=("chatgpt.com",))
                    forced_chatgpt_open = True
                    time.sleep(3)
                    current = str(getattr(driver, "current_url", "") or "")
                except Exception as exc:
                    last_data = f"{type(exc).__name__}: {exc}"
            else:
                time.sleep(1)
                continue

        if 'chatgpt.com' in current:
            try:
                data = _read_chatgpt_session_once(driver)
                if data:
                    return data
                last_data = "session 暂无 accessToken"
            except Exception as exc:
                last_data = f"{type(exc).__name__}: {exc}"
        time.sleep(2)

    raise RuntimeError(f"等待 /api/auth/session accessToken 超时，最后响应: {str(last_data)[:800]}")

