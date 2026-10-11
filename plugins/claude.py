# -*- coding: utf-8 -*-
"""
plugins/claude.py — Claude.ai 自动化注册插件
模式：
  - 基于 BitBrowser / Playwright 指纹环境
  - 邮箱自动填表 -> Magic Link 毫秒级提取 -> 自动手机接码验证
  - 提取并保存 sessionKey，持久化至 accounts 存储
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

logger = logging.getLogger("turb.plugin.claude")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@register_plugin
class ClaudePlugin(BaseSitePlugin):
    """Claude.ai 注册插件。"""

    id = "claude"
    name = "Claude.ai"
    description = "Claude.ai 自动注册，BitBrowser/Playwright 驱动 + Magic Link 邮件激活 + 手机接码 + Cookie 凭据提取"
    target_url = "https://claude.ai"
    table_name = "accounts"
    browser_type = "bitbrowser"
    requires_human = False
    default_headless = False

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        cmd = [sys.executable, "-u", "register.py", "--count", "1"]
        if ctx.proxy:
            cmd += ["--node", ctx.proxy]

        logger.info(f"[Claude] 启动 Claude.ai 核心注册流水线: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            cwd=_ROOT,
            capture_output=False,
            text=True,
        )

        if proc.returncode == 0:
            return RegistrationResult(
                ok=True,
                email=ctx.email or "claude_registered",
                platform=self.id,
                status="active",
            )
        else:
            return RegistrationResult(
                ok=False,
                email=ctx.email,
                platform=self.id,
                error=f"Claude 注册退出码异常: {proc.returncode}",
            )
