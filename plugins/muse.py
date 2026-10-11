# -*- coding: utf-8 -*-
"""
plugins/muse.py — Muse.ai / Muse 自动化注册插件
模式：
  - 自动分配邮箱与强密码
  - 自动穿透表单与收信确权
  - 自动提取登录凭证并同步
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.muse_register_service import MuseRegistrationFlow

logger = logging.getLogger("turb.plugin.muse")


@register_plugin
class MusePlugin(BaseSitePlugin):
    """Muse 注册插件。"""

    id = "muse"
    name = "Muse.ai"
    description = "Muse 音视频与生成式媒体大模型平台，基于邮箱激活确权并提取 Session/API 凭证"
    target_url = "https://muse.ai"
    table_name = "muse_accounts"
    browser_type = "cloak"
    tier = "pure_protocol"
    target_country = "US"
    requires_human = False
    default_headless = True

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        flow = MuseRegistrationFlow(db_path=self.db_path)
        return flow.execute(ctx)

    def push(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> bool:
        from core.muse2api_sync import push_account_to_music2api
        token = result.metadata.get("session_token", "") or result.refresh_token
        cookies = result.cookies or {}
        return push_account_to_music2api(
            email=result.email,
            session_token=token,
            cookies=cookies,
        )
