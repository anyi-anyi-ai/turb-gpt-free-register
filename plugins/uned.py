# -*- coding: utf-8 -*-
"""
plugins/uned.py — UNED (app.uned.es) 西班牙国立远程大学注册插件
模式：
  - 自动准备西班牙合法身分证件 (DNI)
  - Playwright 驱动穿透图形验证码
  - 自动从邮箱池绑定并激活教育学生账号
  - 持久化至 uned_accounts 表
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.uned.registrar import UnedRegistrar

logger = logging.getLogger("turb.plugin.uned")


@register_plugin
class UnedPlugin(BaseSitePlugin):
    """UNED 注册插件。"""

    id = "uned"
    name = "UNED University"
    description = "UNED 西班牙国立远程大学教育身份注册，自动生成合法 DNI、自动过图形验证码并绑定学生账号"
    target_url = "https://app.uned.es/soid"
    table_name = "uned_accounts"
    browser_type = "patchright"
    requires_human = False
    default_headless = True

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        registrar = UnedRegistrar(debug=False)
        res = registrar.register_one(
            headless=ctx.headless,
            proxy_url=ctx.proxy,
        )

        if res.success:
            return RegistrationResult(
                ok=True,
                email=res.student_email or res.email or ctx.email,
                platform=self.id,
                access_token=res.uned_identifier,
                status="active",
                metadata=res.to_dict(),
            )
        else:
            return RegistrationResult(
                ok=False,
                email=res.email or ctx.email,
                platform=self.id,
                error=res.error or "UNED 注册失败",
                metadata=res.to_dict(),
            )
