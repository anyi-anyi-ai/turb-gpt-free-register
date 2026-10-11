# -*- coding: utf-8 -*-
"""
plugins/gmail.py — Gmail Android Appium 本地自动化注册插件
模式：
  - 基于 BlueStacks 模拟器 + Appium 驱动真实 Gmail Android App
  - 自动填写姓名、出生年月日、用户名与强密码
  - 关键安全步骤支持人机协同接管手机验证
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

logger = logging.getLogger("turb.plugin.gmail")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@register_plugin
class GmailPlugin(BaseSitePlugin):
    """Gmail Android 注册插件。"""

    id = "gmail"
    name = "Gmail (Android/Appium)"
    description = "Gmail 谷歌邮箱 Android 本地自动化注册，通过 BlueStacks + Appium 驱动移动端注册"
    target_url = "com.google.android.gm"
    table_name = "email_pool"
    browser_type = "appium"
    requires_human = True  # 声明涉及短信接码人工验证
    default_headless = False

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        script_path = os.path.join(_ROOT, "gmail_android", "gmail_register_local.py")
        cmd = [sys.executable, "-u", script_path]
        logger.info(f"[Gmail] 启动 Gmail Android 本地注册流程: {' '.join(cmd)}")
        proc = subprocess.run(
            cmd,
            cwd=os.path.join(_ROOT, "gmail_android"),
            capture_output=False,
            text=True,
        )

        if proc.returncode == 0:
            return RegistrationResult(
                ok=True,
                email="gmail_registered",
                platform=self.id,
                status="active",
            )
        else:
            return RegistrationResult(
                ok=False,
                email="gmail_failed",
                platform=self.id,
                error=f"Gmail 注册异常退出 (code: {proc.returncode})",
            )
