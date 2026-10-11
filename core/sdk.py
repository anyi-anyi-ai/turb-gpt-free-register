# -*- coding: utf-8 -*-
"""
core/sdk.py — 面向 AI Agent 与外部程序的 Turb 统一异步 Python SDK
允许 Agent 代码无缝导入、调度注册流水线并实时监听进度事件。
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable, Dict, List, Optional

from core.agent_bridge import stream_broadcaster
from core.flows.context import RegistrationResult
from core.plugins.registry import get_plugin_class, list_plugins
from core.vault.identity_vault import IdentitySnapshot, get_identity_vault

logger = logging.getLogger("turb.sdk")


class TurbEngine:
    """Turb 注册引擎 SDK 主入口。"""

    def __init__(self):
        self.vault = get_identity_vault()

    def list_supported_platforms(self) -> List[Dict[str, Any]]:
        """获取所有已装载插件的元数据清单。"""
        return list_plugins()

    async def register(
        self,
        platform: str,
        email: Optional[str] = None,
        country: Optional[str] = None,
        proxy: Optional[str] = None,
        headless: Optional[bool] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> RegistrationResult:
        """
        异步注册单账号任务，支持挂载 Agent 事件监听器。
        """
        plugin_cls = get_plugin_class(platform)
        if not plugin_cls:
            raise ValueError(f"未找到平台注册插件: {platform}")

        plugin = plugin_cls()
        if country:
            plugin.target_country = country

        if on_event:
            stream_broadcaster.add_listener(on_event)

        try:
            stream_broadcaster.emit(
                "sdk_job_started",
                platform=platform,
                target_country=country or getattr(plugin, "target_country", "US"),
            )
            # 在独立工作线程中运行插件同步生命周期
            result = await asyncio.to_thread(
                plugin.run,
                specific_email=email,
                proxy=proxy,
                headless=headless,
            )
            return result
        finally:
            if on_event:
                stream_broadcaster.remove_listener(on_event)

    async def register_batch(
        self,
        platform: str,
        count: int = 1,
        country: Optional[str] = None,
        on_event: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> List[RegistrationResult]:
        """批量注册账号。"""
        results: List[RegistrationResult] = []
        for i in range(count):
            res = await self.register(
                platform=platform,
                country=country,
                on_event=on_event,
            )
            results.append(res)
        return results

    def list_identities(
        self,
        platform: Optional[str] = None,
        country: Optional[str] = None,
    ) -> List[IdentitySnapshot]:
        """查看已保存的身份资产档案。"""
        return self.vault.list_identities(platform=platform, country=country)

    def restore_identity(self, vault_id: str) -> Dict[str, Any]:
        """还原指定账号的同国家 IP 与硬件指纹上下文。"""
        return self.vault.restore_session_context(vault_id)
