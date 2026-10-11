# -*- coding: utf-8 -*-
"""
plugins/proxy_harvester.py — 代理资产全自动收割插件 (Webshare / Resin)
模式：
  - 自动化批量注册 Webshare 账号
  - 自动获取 100~1,000 个纯净住宅代理节点
  - 自动热更至 Resin 代理池并记录账本
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.proxy_harvester import HarvesterController

logger = logging.getLogger("turb.plugin.proxy_harvester")


@register_plugin
class ProxyHarvesterPlugin(BaseSitePlugin):
    """代理自动收割插件。"""

    id = "proxy_harvester"
    name = "Proxy Harvester"
    description = "纯净代理全自动获取机，自动注册 Webshare 提取免费节点并注入 Resin 动态代理池"
    target_url = "https://www.webshare.io"
    table_name = "harvested_proxy_accounts"
    browser_type = "cloak"
    tier = "pure_protocol"
    target_country = "US"
    requires_human = False
    default_headless = True
    manage_driver_internally = True

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        import os
        chosen_provider = os.getenv("PROXY_HARVESTER_PROVIDER", "plainproxies").lower().strip()
        controller = HarvesterController(
            target_accounts=1,
            provider=chosen_provider,
            headless=ctx.headless,
            preferred_email="auto",
            auto_sync_resin=True,
        )
        res = controller.run_harvest()

        if res.get("success") and res.get("total_nodes", 0) > 0:
            return RegistrationResult(
                ok=True,
                email=f"batch_{res.get('batch_id')}",
                platform=self.id,
                access_token=str(res.get("total_nodes")),
                credits=res.get("total_nodes", 0),
                status="active",
                metadata={"batch_id": res.get("batch_id"), "nodes": res.get("total_nodes"), "file_path": res.get("file_path")},
            )
        else:
            return RegistrationResult(
                ok=False,
                email="batch_failed",
                platform=self.id,
                error="未能成功获取有效代理节点",
            )

    def persist(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> None:
        """HarvesterController 内部已自动持久化至 harvested_proxy_batches，无需基类默认 save_account。"""
        logger.info(f"[{self.id}] 代理批次已由控制器持久化落盘: {result.email}")

