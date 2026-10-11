# -*- coding: utf-8 -*-
"""
plugins/suno.py — Suno (suno.com) 自动化注册插件
基于标准插件架构构建：
  - 驱动层：CloakBrowser 指纹浏览器
  - 授权层：自动化穿透 Microsoft OAuth 授权链路
  - 凭据沉淀：提取 Clerk/Suno Session 并持久化
"""
from __future__ import annotations

import logging
import time
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.suno_register_service import SunoRegistrationFlow

logger = logging.getLogger("turb.plugin.suno")


@register_plugin
class SunoPlugin(BaseSitePlugin):
    """Suno 注册插件。"""

    id = "suno"
    name = "Suno AI Music"
    description = "Suno (suno.com) 音乐大模型平台，基于微软 OAuth 链路全自动注册与提取 Clerk 凭据"
    target_url = "https://suno.com/sign-up"
    table_name = "suno_accounts"
    browser_type = "cloak"
    requires_human = False
    default_headless = True

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        # 复用成熟稳定的核心执行流水线
        flow = SunoRegistrationFlow(db_path=self.db_path)
        return flow.execute(ctx)

    def push(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> bool:
        from core.suno2api_sync import push_account_to_suno2api
        token = result.metadata.get("session_token", "") or result.refresh_token
        cookies = result.cookies or {}
        return push_account_to_suno2api(
            email=result.email,
            session_token=token,
            cookies=cookies,
            db_path=self.db_path,
        )
