# -*- coding: utf-8 -*-
"""
plugins/github.py — GitHub (github.com) 自动化注册插件
模式：
  - 驱动层：CloakBrowser 指纹浏览器 + 启发式表单填写
  - 人机协同：Arkose Labs 人机双通道接管 (自动弹出可视化窗口)
  - 邮箱确权：Microsoft Graph API 毫秒级提取 8 位 GitHub Launch Code
  - 质量探针：初生质量探测 (probe_github_account_quality)
  - 凭据沉淀：持久化至 github_accounts 表
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin
from core.outlook_client import get_account_context
from core.github.service import register_github_account_session

logger = logging.getLogger("turb.plugin.github")


@register_plugin
class GitHubPlugin(BaseSitePlugin):
    """GitHub 注册插件。"""

    id = "github"
    name = "GitHub"
    description = "GitHub.com 开发者平台，启发式表单推进 + Arkose 人机拼图协同 + 8位码提取 + 初生探针"
    target_url = "https://github.com/signup"
    table_name = "github_accounts"
    browser_type = "cloak"
    requires_human = True  # 声明需要人机拼图协作
    default_headless = False

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        account = get_account_context(ctx.email)
        if not account:
            raise RuntimeError(f"未找到 {ctx.email} 的 Outlook 上下文凭据")

        proxy_desc = ctx.proxy or "Direct/Local"
        res_dict = register_github_account_session(
            account=account,
            proxy_url=ctx.proxy,
            proxy_desc=proxy_desc,
            headless=ctx.headless,
        )

        status = res_dict.get("status", "unknown")
        if status in ("active", "healthy", "success", "flagged"):
            return RegistrationResult(
                ok=True,
                email=ctx.email,
                platform=self.id,
                access_token=res_dict.get("username", ""),
                cookies=res_dict.get("cookies", {}),
                status=status,
                metadata=res_dict,
            )
        else:
            return RegistrationResult(
                ok=False,
                email=ctx.email,
                platform=self.id,
                error=f"GitHub 注册未达预期状态: {status}",
                metadata=res_dict,
            )
