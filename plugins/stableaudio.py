# -*- coding: utf-8 -*-
"""
plugins/stableaudio.py — Stable Audio (stableaudio.com) 自动化注册插件
模式：
  - 驱动 CloakBrowser
  - 自动化提交表单并处理条款确认
  - 自动收取激活邮件、提取 Session Token 并同步
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.stableaudio_register_service import StableAudioRegistrationFlow

logger = logging.getLogger("turb.plugin.stableaudio")


@register_plugin
class StableAudioPlugin(BaseSitePlugin):
    """Stable Audio 注册插件。"""

    id = "stableaudio"
    name = "Stable Audio"
    description = "Stability AI 旗下 Stable Audio 生成式音频平台，自动注册并提取 Session 与 Cookies"
    target_url = "https://www.stableaudio.com"
    table_name = "stableaudio_accounts"
    browser_type = "cloak"
    requires_human = False
    default_headless = True

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        flow = StableAudioRegistrationFlow(db_path=self.db_path)
        return flow.execute(ctx)

    def push(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> bool:
        from core.stableaudio2api_sync import push_account_to_music2api
        token = result.metadata.get("session_token", "") or result.refresh_token
        cookies = result.cookies or {}
        return push_account_to_music2api(
            email=result.email,
            session_token=token,
            cookies=cookies,
        )
