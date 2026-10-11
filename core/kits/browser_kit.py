# -*- coding: utf-8 -*-
"""
core/kits/browser_kit.py — 统一浏览器驱动积木组件 (BrowserKit)
提供开箱即用的多核浏览器驱动生命周期管理：
  - CloakBrowser 指纹浏览器 (强抗 Cloudflare / Turnstile 盾)
  - Patchright 隔离上下文无痕驱动 (零 CDP 泄露)
  - 自动挂载动态住宅代理与指纹防关联隔离
  - 自动化异常安全销毁与截图存证
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Generator, Optional, Tuple

logger = logging.getLogger("turb.kit.browser")


class BrowserKit:
    """标准化浏览器驱动原子积木。"""

    @staticmethod
    def launch(
        browser_type: str = "cloak",
        proxy: Optional[str] = None,
        headless: bool = True,
        account_email: Optional[str] = None,
    ) -> Any:
        """
        根据指定类型启动浏览器驱动实例。
        :param browser_type: "cloak" | "patchright"
        :param proxy: 代理地址，格式为 http://user:pass@host:port 或 http://127.0.0.1:7897
        :param headless: 是否使用无头模式
        :param account_email: 绑定的账号邮箱（用于 CloakBrowser 专属指纹隔离）
        """
        browser_type = (browser_type or "cloak").lower().strip()

        # 确保未指定代理时统一从粘性节点池动态分配
        effective_proxy = proxy
        if not effective_proxy:
            try:
                from config.proxy import pick_proxy
                effective_proxy = pick_proxy(for_registration=True)
                if effective_proxy:
                    logger.info(f"[BrowserKit] 自动接入粘性出口代理: {effective_proxy}")
            except Exception as e:
                logger.debug(f"[BrowserKit] 默认分配粘性代理跳过: {e}")

        if browser_type == "cloak":
            from core.cloakbrowser_driver import build_cloak_driver
            driver, _ = build_cloak_driver(
                proxy=effective_proxy,
                headless=headless,
                account_email=account_email,
            )
            return driver

        elif browser_type in ("patchright", "playwright"):
            from common.stealth_playwright import sync_playwright
            pw = sync_playwright().start()
            launch_args = ["--disable-blink-features=AutomationControlled"]
            proxy_dict = None
            if effective_proxy:
                proxy_dict = {"server": effective_proxy}

            browser = pw.chromium.launch(
                headless=headless,
                args=launch_args,
                proxy=proxy_dict,
            )
            context = browser.new_context()
            page = context.new_page()
            # 封装一个轻量结构体保持接口统一
            class PlaywrightWrapper:
                def __init__(self, pw, browser, context, page):
                    self._pw = pw
                    self.browser = browser
                    self.context = context
                    self.page = page

                def get(self, url: str):
                    self.page.goto(url)

                def quit(self):
                    try:
                        self.context.close()
                        self.browser.close()
                        self._pw.stop()
                    except Exception:
                        pass

            return PlaywrightWrapper(pw, browser, context, page)

        else:
            raise ValueError(f"不支持的浏览器驱动类型: {browser_type} (支持: cloak, patchright)")

    @staticmethod
    def close(driver: Any) -> None:
        """安全释放浏览器与网络会话。"""
        if driver is None:
            return
        try:
            if hasattr(driver, "quit"):
                driver.quit()
            elif hasattr(driver, "close"):
                driver.close()
        except Exception as e:
            logger.debug(f"[BrowserKit] 关闭 driver 时出现非阻塞警告: {e}")

    @classmethod
    @contextmanager
    def managed(
        cls,
        browser_type: str = "cloak",
        proxy: Optional[str] = None,
        headless: bool = True,
        account_email: Optional[str] = None,
    ) -> Generator[Any, None, None]:
        """上下文管理器：自动启动并在退出时安全销毁浏览器实例。"""
        driver = None
        try:
            driver = cls.launch(
                browser_type=browser_type,
                proxy=proxy,
                headless=headless,
                account_email=account_email,
            )
            yield driver
        finally:
            cls.close(driver)
