# -*- coding: utf-8 -*-
"""
plugins/kiro.py — AWS Builder ID / Kiro 自动化注册插件
模式：
  - 纯 HTTP / TLS 协议级极速注册 (基于 curl_cffi 与 AWS OIDC 逆向)
  - 自动收发验证码
  - 自动导出 Kiro RS 凭证
"""
from __future__ import annotations

import argparse
import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.outlook_client import get_account_context
import register_kiro

logger = logging.getLogger("turb.plugin.kiro")


@register_plugin
class KiroPlugin(BaseSitePlugin):
    """Kiro / AWS Builder ID 注册插件。"""

    id = "kiro"
    name = "AWS Builder ID (Kiro)"
    description = "AWS Builder ID 协议级注册，自动化收发验证码并导出 Kiro IDE 授权凭证"
    target_url = "https://view.awsapps.com/start"
    table_name = "kiro_accounts"
    browser_type = "cloak"
    requires_human = False
    default_headless = True

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        account = get_account_context(ctx.email)
        if not account:
            raise RuntimeError(f"未找到 {ctx.email} 的 Outlook 上下文凭据")

        dummy_args = argparse.Namespace(
            account_password=ctx.password,
            full_name="Kiro Developer",
            timeout=120,
            keep_on_fail=False,
        )

        try:
            res = register_kiro.register_one(
                email=account.email,
                mailbox_password=account.password,
                mailbox_token=account.refresh_token,
                mailbox_client_id=account.client_id,
                args=dummy_args,
            )
            return RegistrationResult(
                ok=True,
                email=ctx.email,
                platform=self.id,
                access_token=str(res) if res else "Registered",
                status="active",
            )
        except Exception as e:
            return RegistrationResult(
                ok=False,
                email=ctx.email,
                platform=self.id,
                error=str(e),
            )
