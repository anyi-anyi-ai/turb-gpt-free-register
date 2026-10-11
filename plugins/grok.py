# -*- coding: utf-8 -*-
"""
plugins/grok.py — xAI Grok (grok.com) 自动化注册插件
模式：
  - 驱动层：CloakBrowser 指纹浏览器
  - 流程：自动过 Cloudflare 盾 -> 填邮箱 -> 毫秒级收取验证码 -> 个人资料生成
  - 授权：OAuth Device 授权换取持久 Refresh Token
  - 自检：GrokQualityChecker 降智防护探针
  - 沉淀：入库 grok_accounts 并支持 sub2api / gptgrok2api 同步
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.grok.registration_service import register_single_grok_account

logger = logging.getLogger("turb.plugin.grok")


@register_plugin
class GrokPlugin(BaseSitePlugin):
    """xAI Grok 注册插件。"""

    id = "grok"
    name = "xAI Grok"
    description = "xAI Grok 大模型平台，全自动过盾 + 验证码自动提取 + Device 授权换取 RefreshToken + 初生探针"
    target_url = "https://grok.com"
    table_name = "grok_accounts"
    browser_type = "cloak"
    requires_human = False
    default_headless = True

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        res = register_single_grok_account(
            proxy=ctx.proxy,
            headless=ctx.headless,
        )

        if res.get("success"):
            return RegistrationResult(
                ok=True,
                email=res.get("email", ctx.email),
                platform=self.id,
                access_token=res.get("sso_token", ""),
                refresh_token=res.get("refresh_token", ""),
                status=res.get("quality_status", "active"),
                metadata=res,
            )
        else:
            return RegistrationResult(
                ok=False,
                email=res.get("email", ctx.email),
                platform=self.id,
                error=res.get("error", "未知注册错误"),
                metadata=res,
            )
