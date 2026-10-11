# -*- coding: utf-8 -*-
"""
plugins/elevenlabs.py — ElevenLabs (elevenlabs.io) 自动化注册插件
模式：
  - 自动分配邮箱与强密码并填报表单
  - 触发 Arkose 拼图时自动转为可视化窗口支持极速人机协作
  - 自动通过 Graph API 毫秒级轮询收信激活
  - 提取 API Key 与 Session 凭据落盘推送到网关
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.elevenlabs_register_service import ElevenLabsRegistrationFlow

logger = logging.getLogger("turb.plugin.elevenlabs")


@register_plugin
class ElevenLabsPlugin(BaseSitePlugin):
    """ElevenLabs 注册插件。"""

    id = "elevenlabs"
    name = "ElevenLabs Voice AI"
    description = "ElevenLabs 语音合成大模型平台，半自动人机协同拼图 + 毫秒级 Graph 收信激活并提取 API Key"
    target_url = "https://elevenlabs.io/sign-up"
    table_name = "elevenlabs_accounts"
    browser_type = "cloak"
    requires_human = True  # 声明需要人机拼图协作，调度器会自动以有头模式启动并给出友好提示
    default_headless = False

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        flow = ElevenLabsRegistrationFlow(db_path=self.db_path)
        return flow.execute(ctx)

    def push(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> bool:
        from core.elevenlabs2api_sync import push_account_to_music2api
        api_key = result.metadata.get("api_key", "")
        token = result.metadata.get("session_token", "")
        cookies = result.cookies or {}
        return push_account_to_music2api(
            email=result.email,
            api_key=api_key,
            session_token=token,
            credits=result.credits,
            cookies=cookies,
        )
