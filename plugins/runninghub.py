# -*- coding: utf-8 -*-
"""
plugins/runninghub.py — RunningHub (runninghub.cn/com) 自动化注册插件
模式：
  - 自动分配邮箱与密码
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
from core.runninghub_register_service import RunningHubRegistrationFlow

logger = logging.getLogger("turb.plugin.runninghub")


@register_plugin
class RunningHubPlugin(BaseSitePlugin):
    """RunningHub 注册插件。"""

    id = "runninghub"
    name = "RunningHub AI"
    description = "RunningHub AI 工作流与模型生成平台，全自动注册与凭据沉淀"
    target_url = "https://www.runninghub.cn"
    table_name = "runninghub_accounts"
    browser_type = "cloak"
    requires_human = True  # 声明需要人机滑块协作，中枢自动弹出可视化窗口
    default_headless = False

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        flow = RunningHubRegistrationFlow(db_path=self.db_path)
        return flow.execute(ctx)

    def push(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> bool:
        from core.runninghub2api_sync import push_account_to_runninghub2api
        token = result.metadata.get("session_token", "") or result.refresh_token
        cookies = result.cookies or {}
        return push_account_to_runninghub2api(
            email=result.email,
            session_token=token,
            cookies=cookies,
        )
