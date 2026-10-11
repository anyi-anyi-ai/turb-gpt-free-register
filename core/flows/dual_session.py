# -*- coding: utf-8 -*-
"""
core/flows/dual_session.py — 协议/浏览器轻重双模统一会话 (DualSession)
吸收开源项目 DrissionPage 核心设计哲学：
1. 协议先行：默认使用高性能、低开销的 TLS Impersonate 会话 (curl_cffi Chrome124 / JA4 指纹拟态)；
2. 无感共享：协议层与浏览器层共享同一个 Cookie Jar、User-Agent 与代理出口国家；
3. 动态升阶自愈：当协议请求遭遇 Cloudflare 5秒盾、Wasm PoW 或人机挑战时，自动无感激活 Camoufox / Patchright 接管过盾，
   穿透后自动将生成的 cf_clearance / 认证凭据同步回协议会话，实现轻重平滑闭环。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional, Tuple, Union

try:
    from curl_cffi import requests as cffi_requests
    from curl_cffi.requests import AsyncSession as CffiAsyncSession
except ImportError:
    cffi_requests = None
    CffiAsyncSession = None

logger = logging.getLogger("turb.flows.dual_session")


class DualSession:
    """双模统一轻重会话管理器。"""

    def __init__(
        self,
        target_country: str = "US",
        proxy: Optional[str] = None,
        user_agent: Optional[str] = None,
        impersonate: str = "chrome124",
        browser_type: str = "camoufox",
    ):
        self.target_country = target_country
        self.proxy = proxy
        self.user_agent = user_agent or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        )
        self.impersonate = impersonate
        self.browser_type = browser_type

        # 协议底层 Client
        self._cffi_session: Optional[Any] = None
        # 激活的浏览器 Page / Context 句柄
        self._browser_context: Optional[Any] = None
        self._active_page: Optional[Any] = None

    def _ensure_cffi_session(self) -> Any:
        if self._cffi_session is None:
            if cffi_requests:
                proxies = {"http": self.proxy, "https": self.proxy} if self.proxy else None
                headers = {
                    "User-Agent": self.user_agent,
                    "Accept-Language": "en-US,en;q=0.9",
                }
                self._cffi_session = cffi_requests.Session(
                    impersonate=self.impersonate,
                    proxies=proxies,
                    headers=headers,
                    timeout=30,
                )
            else:
                import requests
                self._cffi_session = requests.Session()
                if self.proxy:
                    self._cffi_session.proxies = {"http": self.proxy, "https": self.proxy}
                self._cffi_session.headers.update({"User-Agent": self.user_agent})
        return self._cffi_session

    # -------------------------------------------------------------
    # 协议轻量方法 (Protocol Methods)
    # -------------------------------------------------------------
    def get(self, url: str, **kwargs) -> Any:
        """协议 GET 请求。"""
        sess = self._ensure_cffi_session()
        resp = sess.get(url, **kwargs)
        self._check_and_log_response(url, resp)
        return resp

    def post(self, url: str, **kwargs) -> Any:
        """协议 POST 请求。"""
        sess = self._ensure_cffi_session()
        resp = sess.post(url, **kwargs)
        self._check_and_log_response(url, resp)
        return resp

    def get_cookies_dict(self) -> Dict[str, str]:
        """提取协议层当前的 Cookie 字典。"""
        sess = self._ensure_cffi_session()
        if hasattr(sess.cookies, "get_dict"):
            return sess.cookies.get_dict()
        return dict(sess.cookies)

    def set_cookies(self, cookies: Union[Dict[str, str], List[Dict[str, Any]]]) -> None:
        """更新协议层 Cookie。"""
        sess = self._ensure_cffi_session()
        if isinstance(cookies, dict):
            for k, v in cookies.items():
                sess.cookies.set(k, v)
        elif isinstance(cookies, list):
            for c in cookies:
                name = c.get("name")
                val = c.get("value")
                domain = c.get("domain")
                if name and val:
                    sess.cookies.set(name, val, domain=domain)

    # -------------------------------------------------------------
    # 双向 Cookie 共享 (Shared Cookie Mechanism)
    # -------------------------------------------------------------
    async def sync_to_browser(self, browser_context: Any) -> None:
        """将当前协议层 Cookie 镜像写入 Playwright 浏览器 Context。"""
        cookies_dict = self.get_cookies_dict()
        if not cookies_dict or not browser_context:
            return

        playwright_cookies = []
        for name, value in cookies_dict.items():
            playwright_cookies.append({
                "name": name,
                "value": value,
                "domain": "",  # 浏览器将自动绑定至当前主机
                "path": "/",
            })
        try:
            # 过滤合法格式后注入
            valid_cookies = [
                {"name": k, "value": v, "url": "https://localhost"}
                for k, v in cookies_dict.items()
            ]
            # 若已有激活页面，从页面当前 URL 注入
            if self._active_page:
                current_url = self._active_page.url
                if current_url and current_url.startswith("http"):
                    valid_cookies = [
                        {"name": k, "value": v, "url": current_url}
                        for k, v in cookies_dict.items()
                    ]
            await browser_context.add_cookies(valid_cookies)
            logger.info(f"[DualSession] 协议层 -> 浏览器: 成功同步 {len(valid_cookies)} 条 Cookies")
        except Exception as e:
            logger.debug(f"[DualSession] 同步 Cookie 到浏览器警告: {e}")

    async def sync_from_browser(self, browser_context: Any) -> None:
        """将浏览器 Context 产生的新 Cookie (如 cf_clearance, _px3) 镜像写回协议层。"""
        if not browser_context:
            return
        try:
            b_cookies = await browser_context.cookies()
            self.set_cookies(b_cookies)
            logger.info(f"[DualSession] 浏览器 -> 协议层: 成功同步 {len(b_cookies)} 条 Cookies")
        except Exception as e:
            logger.warning(f"[DualSession] 从浏览器同步 Cookie 异常: {e}")

    # -------------------------------------------------------------
    # 自愈升阶机制 (Dynamic Escalation)
    # -------------------------------------------------------------
    def is_blocked(self, response: Any) -> bool:
        """判定响应是否遭遇 Cloudflare 5秒盾、WAF 或人机验证拦截。"""
        if response is None:
            return False
        status = getattr(response, "status_code", 0)
        text = str(getattr(response, "text", "") or "").lower()
        if status in (403, 429, 503):
            if any(marker in text for marker in ("cloudflare", "turnstile", "just a moment", "perimeterx", "attention required")):
                return True
        return False

    async def escalate_to_browser(
        self,
        url: str,
        kits: Any,
        solve_captcha: bool = True,
    ) -> Any:
        """
        核心自愈方法：协议遭遇阻断时，无感启动同指纹、同代理的无痕浏览器接管。
        穿透后同步 Cookie 并返回当前激活的 Page 句柄。
        """
        logger.info(f"[DualSession] 🚀 触发动态自愈升阶: 协议模式受阻，唤起 {self.browser_type} 接管页面: {url}")
        page = await kits.browser.get_page(
            browser_type=self.browser_type,
            target_country=self.target_country,
        )
        self._active_page = page
        context = page.context
        self._browser_context = context

        # 1. 注入当前协议 Cookie
        await self.sync_to_browser(context)

        # 2. 浏览器导航至目标页面
        await page.goto(url, wait_until="domcontentloaded", timeout=45000)

        # 3. 若检测到 Cloudflare Turnstile 等挑战，自动调用 CaptchaKit 穿透
        if solve_captcha:
            cf_detected = await page.locator("iframe[src*='challenges.cloudflare'], .cf-turnstile").count() > 0
            if cf_detected:
                logger.info("[DualSession] 浏览器检测到 Turnstile 挑战，自动调度 CaptchaKit 穿透...")
                await kits.captcha.handle_turnstile(page, timeout=30)
                await asyncio.sleep(2)

        # 4. 穿透完成后，将新鲜 Cookie 同步回协议层
        await self.sync_from_browser(context)
        logger.info("[DualSession] 自愈完成，轻重会话已重新对齐。")
        return page

    def _check_and_log_response(self, url: str, resp: Any) -> None:
        status = getattr(resp, "status_code", 0)
        if self.is_blocked(resp):
            logger.warning(f"[DualSession] 请求可能遭遇风控拦截: {url} -> HTTP {status}")
        else:
            logger.debug(f"[DualSession] 协议响应: {url} -> HTTP {status}")

    async def close(self) -> None:
        """优雅关闭会话资源。"""
        if self._cffi_session and hasattr(self._cffi_session, "close"):
            try:
                self._cffi_session.close()
            except Exception:
                pass
        self._active_page = None
        self._browser_context = None
