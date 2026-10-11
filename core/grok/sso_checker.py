# -*- coding: utf-8 -*-
"""轻量级 Grok SSO 账号存活性与状态巡检模块。

无需启动 CloakBrowser 重量级指纹浏览器，基于 curl_cffi 模拟真实 Chrome TLS/JA3 指纹，
秒级并发完成存量账号的存活判定（有效/过期/封禁/降智）。
代理路由遵循现有系统规范：优先使用账号已记录的 proxy_used，缺省回退到系统代理池。
"""
from __future__ import annotations

import json
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

from core.chatgpt_plan import resolve_plan_check_route, _mask_proxy

logger = logging.getLogger(__name__)

HOME_URL = "https://grok.com/"
DEFAULT_TIMEOUT_SEC = 15


@dataclass
class SsoCheckResult:
    ok: bool = False
    status: str = "unknown"  # healthy / expired / bot_flagged / degraded / error
    email: str = ""
    account_id: int = 0
    user_id: str = ""
    bot_flag_source: int | None = None
    bot_flag_details: str = ""
    proxy_used: str = ""
    error: str = ""
    raw_user: dict[str, Any] = field(default_factory=dict)


class GrokSsoChecker:
    """基于 curl_cffi 的无头轻量 SSO 存活校验器。"""

    def __init__(self, timeout_sec: int = DEFAULT_TIMEOUT_SEC):
        self.timeout_sec = timeout_sec

    def check_account(
        self,
        account: dict[str, Any],
        explicit_proxy: str | None = None,
    ) -> SsoCheckResult:
        """检查单个 Grok 账号的 SSO 存活状态。"""
        acc_id = int(account.get("id") or 0)
        email = str(account.get("email") or "").strip()
        sso_token = str(account.get("sso_token") or account.get("sso_rw") or "").strip()
        if sso_token.startswith("sso="):
            sso_token = sso_token[4:].strip()

        result = SsoCheckResult(account_id=acc_id, email=email)

        if not sso_token:
            result.status = "expired"
            result.error = "账号缺少 sso_token 凭据"
            return result

        if curl_requests is None:
            result.status = "error"
            result.error = "未安装 curl_cffi 依赖，无法执行轻量 TLS 请求"
            return result

        # 1. 路由解析 (参考 ChatGPT 账号的代理路由逻辑)
        route = resolve_plan_check_route(explicit_proxy=explicit_proxy, account=account)
        selected_proxy = route.get("proxy") or ""
        result.proxy_used = _mask_proxy(selected_proxy)

        # 2. 构建模拟 Chrome 浏览器会话
        session = curl_requests.Session(impersonate="chrome124")
        if selected_proxy:
            session.proxies = {"http": selected_proxy, "https": selected_proxy}

        cookies = {"sso": sso_token, "sso-rw": sso_token}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Cache-Control": "no-cache",
        }

        try:
            resp = session.get(HOME_URL, cookies=cookies, headers=headers, timeout=self.timeout_sec)
            final_url = str(resp.url or "")
            html_text = resp.text or ""

            # 3. 判定是否被重定向到登录页或明确 401/403
            if "accounts.x.ai" in final_url or "sign-in" in final_url or resp.status_code in (401, 403):
                result.status = "expired"
                result.error = f"登录凭据已失效 (HTTP {resp.status_code}, 跳转至登录)"
                return result

            if resp.status_code != 200:
                result.status = "error"
                result.error = f"首页请求异常: HTTP {resp.status_code}"
                return result

            # 4. 解析 initialData 中的用户信息与风控标记
            user_info = self._parse_user_data(html_text)
            if not user_info:
                # 检查页面是否包含明确未登录特征
                if "sign-in" in html_text or "Log in" in html_text:
                    result.status = "expired"
                    result.error = "页面显示未登录状态 (SSO Cookie 已失效)"
                    return result
                result.status = "healthy"
                result.ok = True
                return result

            result.raw_user = user_info
            result.user_id = str(user_info.get("id") or "")
            bot_source, bot_details = self._parse_bot_flags(user_info, html_text)
            result.bot_flag_source = bot_source
            result.bot_flag_details = bot_details

            if bot_source is not None and bot_source != 0:
                result.status = "bot_flagged"
                result.error = f"已命中 Bot 标记 (source={bot_source})"
                result.ok = False
            elif "policy=deny" in bot_details.lower():
                result.status = "bot_flagged"
                result.error = "已命中 policy=deny 拒绝策略"
                result.ok = False
            else:
                result.status = "healthy"
                result.ok = True

            return result

        except Exception as exc:
            err_msg = str(exc)
            result.status = "error"
            result.error = f"网络请求失败: {err_msg[:120]}"
            return result

    @staticmethod
    def _parse_user_data(html: str) -> dict[str, Any] | None:
        marker = '"initialData":'
        pos = html.find(marker)
        if pos < 0:
            return None
        sub = html[pos + len(marker):].lstrip()
        decoder = json.JSONDecoder()
        try:
            val, _ = decoder.raw_decode(sub)
            if isinstance(val, dict):
                return val.get("user") or (val if "id" in val else None)
        except Exception:
            pass
        return None

    @staticmethod
    def _parse_bot_flags(user_data: dict[str, Any] | None, html: str) -> tuple[int | None, str]:
        source = None
        details = ""
        if user_data:
            raw_src = user_data.get("botFlagSource")
            if raw_src is not None:
                try:
                    source = int(raw_src)
                except (ValueError, TypeError):
                    pass
            details = str(user_data.get("botFlagDetails") or "").strip()

        if source is None:
            m = re.search(r'botFlagSource"\s*:\s*(null|-?\d+)', html)
            if m and m.group(1) != "null":
                try:
                    source = int(m.group(1))
                except ValueError:
                    pass
            m2 = re.search(r'botFlagDetails"\s*:\s*(?:null|"((?:\\.|[^"\\])*)")', html)
            if m2 and m2.group(1):
                details = m2.group(1)

        return source, details


def batch_check_grok_liveness(
    accounts: list[dict[str, Any]],
    concurrency: int = 5,
    explicit_proxy: str | None = None,
    log_callback: Callable[[str], None] | None = None,
) -> list[SsoCheckResult]:
    """多线程并发执行 Grok 账号快速批量测活。"""
    checker = GrokSsoChecker()
    results: list[SsoCheckResult] = []
    safe_concurrency = max(1, min(int(concurrency or 5), 10, len(accounts) or 1))

    def _log(msg: str):
        if log_callback:
            log_callback(msg)
        logger.info(f"[GrokLiveness] {msg}")

    _log(f"开始执行 Grok 账号批量快速测活: 总数={len(accounts)}, 并发 Worker={safe_concurrency}")

    with ThreadPoolExecutor(max_workers=safe_concurrency, thread_name_prefix="grok-live") as executor:
        future_map = {
            executor.submit(checker.check_account, acc, explicit_proxy): acc
            for acc in accounts
        }
        done_count = 0
        for future in as_completed(future_map):
            done_count += 1
            acc = future_map[future]
            try:
                res = future.result()
            except Exception as exc:
                res = SsoCheckResult(
                    account_id=int(acc.get("id") or 0),
                    email=str(acc.get("email") or ""),
                    status="error",
                    error=str(exc),
                )
            results.append(res)
            _log(f"[{done_count}/{len(accounts)}] {res.email}: status={res.status} ({res.error or '正常'})")

    return results
