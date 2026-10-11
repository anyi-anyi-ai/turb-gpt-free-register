# -*- coding: utf-8 -*-
"""
plugins/chatgpt.py — ChatGPT / OpenAI 官方自动化注册插件
模式：
  - 复用 common 基建 (BitBrowser / stealth / Outlook 取验证码 / Cookie 保存)
  - 流程：chatgpt.com/auth/login -> 填邮箱 -> 验证码/密码 -> Arkose 穿透 -> onboarding -> 保存 Cookie
  - 支持 Codex OAuth 授权与 SUB2API 自动导入
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin

logger = logging.getLogger("turb.plugin.chatgpt")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@register_plugin
class ChatGPTPlugin(BaseSitePlugin):
    """ChatGPT / OpenAI 注册插件。"""

    id = "chatgpt"
    name = "ChatGPT (OpenAI)"
    description = "ChatGPT / OpenAI 官方自动注册，BitBrowser+Playwright 驱动 + 邮箱验证码/密码 + Onboarding + 凭据导出"
    target_url = "https://chatgpt.com/auth/login"
    table_name = "codex_accounts"
    browser_type = "bitbrowser"
    requires_human = False
    default_headless = False

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        cmd = [sys.executable, "-u", "register_chatgpt.py", "--count", "1"]
        if ctx.email:
            cmd += ["--email", ctx.email]
        if ctx.proxy:
            cmd += ["--node", ctx.proxy]

        logger.info(f"[ChatGPT] 启动 ChatGPT 注册流程: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            cwd=_ROOT,
            capture_output=False,
            text=True,
        )

        if proc.returncode == 0:
            return RegistrationResult(
                ok=True,
                email=ctx.email or "chatgpt_registered",
                platform=self.id,
                status="active",
            )
        else:
            return RegistrationResult(
                ok=False,
                email=ctx.email,
                platform=self.id,
                error=f"ChatGPT 注册异常中断 (code: {proc.returncode})",
            )
