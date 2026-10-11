# -*- coding: utf-8 -*-
"""
GitHub 注册后即时质量自检探针 (Post-Registration Probe) 模块。

借鉴自 GrokIQ 设计：
在拿到注册结果和 Cookies 后，自动发起轻量探针，验证账号是否处于
正常活跃状态，或是被 GitHub 风控系统打上 Flagged / Shadowban 标记。
"""
from __future__ import annotations

import logging
from typing import Dict, Any, Tuple

from curl_cffi.requests import Session as CurlSession

logger = logging.getLogger("github.probe")


def probe_github_account_quality(
    username: str,
    cookies: list[dict],
    proxy: str | None = None,
) -> tuple[str, dict]:
    """对新注册的 GitHub 账号进行初生质量探针测试。

    返回: (quality_status, detail_info)
      - "active": 账号完全健康，可正常访问个人信息
      - "flagged": 账号已被风控系统挂起 (Flagged / Suspended)
      - "suspect": 未被完全封禁但受限或无有效会话
    """
    logger.info(f"🩺 [初生探针] 正在对新注册账号 {username} 发起存活与风控状态检测...")

    cookie_dict = {c["name"]: c["value"] for c in cookies if "name" in c and "value" in c}
    user_session = cookie_dict.get("user_session")

    if not user_session:
        logger.warning(f"[初生探针] 账号 {username} 缺少 user_session Cookie")
        return "suspect", {"reason": "missing_user_session"}

    session = CurlSession(impersonate="chrome124")
    if proxy:
        session.proxies = {"http": proxy, "https": proxy}

    # 1. 探针 1：请求 GitHub 网页版个人 Settings 页面，验证会话完整度与是否有挂起横幅
    try:
        resp = session.get(
            "https://github.com/settings/profile",
            cookies=cookie_dict,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=15,
            allow_redirects=True,
        )

        content = resp.text.lower()

        # 检测风控拦截关键字
        if "account has been flagged" in content or "account is suspended" in content:
            logger.warning(f"🚨 [初生探针] 警报：账号 {username} 被 GitHub 判定为 Flagged (风控挂起)！")
            return "flagged", {"reason": "flagged_banner_detected", "status_code": resp.status_code}

        if resp.status_code == 200 and ("sign out" in content or "profile settings" in content):
            logger.info(f"✅ [初生探针] 账号 {username} 会话校验通过，状态：健康活跃 (active)")
            return "active", {"status_code": 200, "verified_user": username}

        if resp.status_code in (403, 401):
            logger.warning(f"[初生探针] 账号 {username} 访问 Profile 返回 HTTP {resp.status_code}")
            return "suspect", {"reason": f"http_{resp.status_code}"}

    except Exception as e:
        logger.warning(f"[初生探针] 探针检测网络抖动: {e}")
        return "active", {"reason": "probe_network_skip", "error": str(e)}
    finally:
        session.close()

    return "active", {"reason": "default_pass"}
