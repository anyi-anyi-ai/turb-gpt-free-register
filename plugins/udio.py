# -*- coding: utf-8 -*-
"""
plugins/udio.py — Udio (udio.com) 自动化注册插件
模式：
  - 自动打开 Udio 提交邮箱
  - 自动通过 Graph API 轮询收信并点击 Magic Link
  - 自动初始化档案并抽取 Supabase Auth Token
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.udio_register_service import UdioRegistrationFlow

logger = logging.getLogger("turb.plugin.udio")


@register_plugin
class UdioPlugin(BaseSitePlugin):
    """Udio 注册插件。"""

    id = "udio"
    name = "Udio AI Music"
    description = "Udio (udio.com) AI 音乐生成平台，通过 Magic Link 邮件激活，自动初始化档案并提取 Token"
    target_url = "https://www.udio.com"
    table_name = "udio_accounts"
    browser_type = "cloak"
    requires_human = False
    default_headless = True

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        flow = UdioRegistrationFlow(db_path=self.db_path)
        return flow.execute(ctx)

    def push(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> bool:
        from core.udio2api_sync import push_account_to_music2api
        token = result.metadata.get("session_token", "") or result.refresh_token
        cookies = result.cookies or {}
        return push_account_to_music2api(
            email=result.email,
            session_token=token,
            cookies=cookies,
        )
