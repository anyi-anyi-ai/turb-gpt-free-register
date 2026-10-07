# -*- coding: utf-8 -*-
"""隐身自动化引擎适配器 (Stealth Playwright / Patchright Bridge)

深度借鉴与集成:
references/patchright-python (Kaliiiiiiiiii-Vinyzu/patchright)
彻底消除 Playwright 在 CDP 层的 Runtime.enable / Console.enable 特征泄漏。
作为项目全局 async_playwright / sync_playwright 的无感平替分发器。
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger("stealth_playwright")

_USE_PATCHRIGHT = str(os.environ.get("DISABLE_PATCHRIGHT", "0")).lower() not in ("1", "true", "yes")

try:
    if _USE_PATCHRIGHT:
        from patchright.async_api import async_playwright as _async_pw
        from patchright.sync_api import sync_playwright as _sync_pw
        _ENGINE = "patchright"
        logger.info("🛡️ [隐身引擎] 已成功载入 Patchright (隔离上下文 CDP 无泄漏模式)")
    else:
        raise ImportError("Patchright explicitly disabled via environment")
except Exception as _e:
    from playwright.async_api import async_playwright as _async_pw
    from playwright.sync_api import sync_playwright as _sync_pw
    _ENGINE = "playwright"
    if not _USE_PATCHRIGHT:
        logger.info("ℹ️ [隐身引擎] 用户禁用 Patchright，使用标准 Playwright 引擎")
    else:
        logger.warning(f"⚠️ [隐身引擎] Patchright 加载失败 ({_e})，自动降级为标准 Playwright 引擎 (可能降低抗封禁效果)")

async_playwright = _async_pw
sync_playwright = _sync_pw


def is_patchright_active() -> bool:
    """返回当前是否正在运行 Patchright 防泄露引擎"""
    return _ENGINE == "patchright"


def engine_name() -> str:
    """返回当前生效的引擎名称"""
    return _ENGINE
