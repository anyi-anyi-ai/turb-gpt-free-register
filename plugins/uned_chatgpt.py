# -*- coding: utf-8 -*-
"""
plugins/uned_chatgpt.py — UNED 学生邮箱 ChatGPT 协同注册插件
模式：
  - 使用已激活的 UNED 学生邮箱进行 ChatGPT 官方注册
  - 自动轮换 Grade A 干净代理出口
  - 微软 Graph API 接码并沉淀 ChatGPT 凭据
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

logger = logging.getLogger("turb.plugin.uned_chatgpt")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@register_plugin
class UnedChatGPTPlugin(BaseSitePlugin):
    """UNED ChatGPT 注册插件。"""

    id = "uned_chatgpt"
    name = "UNED ChatGPT"
    description = "UNED 西班牙学生学籍注册 ChatGPT / Plus 协同工作流，Graph API 接码并沉淀 ChatGPT 凭据"
    target_url = "https://chatgpt.com/auth/login"
    table_name = "codex_accounts"
    browser_type = "cloak"
    requires_human = False
    default_headless = False

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        cmd = [sys.executable, "-u", "run_uned_chatgpt_register.py", "--count", "1"]
        logger.info(f"[UNED-ChatGPT] 启动 UNED 学生邮箱注册 ChatGPT 流程: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            cwd=_ROOT,
            capture_output=False,
            text=True,
        )

        if proc.returncode == 0:
            return RegistrationResult(
                ok=True,
                email=ctx.email or "uned_chatgpt_registered",
                platform=self.id,
                status="active",
            )
        else:
            return RegistrationResult(
                ok=False,
                email=ctx.email,
                platform=self.id,
                error=f"UNED ChatGPT 注册退出异常 (code: {proc.returncode})",
            )
