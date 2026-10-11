# -*- coding: utf-8 -*-
"""
plugins/outlook.py — Outlook 微软官方邮箱造血注册插件
模式：
  - 自动化批量注册微软 Outlook 邮箱
  - 自动穿透微软 Arkose Labs / 拼图与长按验证
  - 自动授权并换取 Microsoft Graph API Refresh Token
  - 直接沉淀至本地 email_pool 邮箱库，为全平台注册提供源源不断弹药
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

logger = logging.getLogger("turb.plugin.outlook")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@register_plugin
class OutlookPlugin(BaseSitePlugin):
    """Outlook 邮箱造血注册插件。"""

    id = "outlook"
    name = "Outlook (Microsoft)"
    description = "Outlook 微软官方邮箱自动化造血注册，自动过验证并提取 Graph API Refresh Token 注入邮箱池"
    target_url = "https://signup.live.com"
    table_name = "email_pool"
    browser_type = "cloak"
    manage_driver_internally = True
    requires_human = False
    default_headless = False
    target_country = "DE"

    def pick_account(self, specific_email: Any = None) -> Dict[str, Any]:
        """Outlook 为造血机平台，自动生成新邮箱账号，不消费现有邮箱池。"""
        return {
            "email": specific_email or "outlook_auto_generate",
            "payload": {},
        }

    def persist(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> bool:
        """register_outlook_standalone 内部已自动调用 upsert_outlook_with_graph 持久化至 email_pool。"""
        return True

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        count = ctx.metadata.get("count", 1)
        cmd = [
            sys.executable, "-u", "register_outlook_standalone.py",
            "--count", str(count),
            "--mode", "cloak",
            "--concurrency", "1",
            "--timeout", "600",
        ]
        env_copy = os.environ.copy()
        env_copy["FINGERPRINT_BROWSER"] = "cloak"
        env_copy["BROWSER_PROVIDER"] = "cloak"
        env_copy["TRAFFIC_SAVER"] = "off"
        env_copy["REG_FACTORY_RESIDENTIAL_TRAFFIC_MODE"] = "off"
        env_copy["REG_FACTORY_PLATFORM"] = "outlook"
        env_copy["CLOAK_LOCALE"] = "en-US"
        env_copy["CLOAK_TIMEZONE"] = "America/New_York" if self.target_country == "US" else "Europe/Paris"
        
        # 强制清除宿主机房 7890 代理污染，严格使用粘性住宅节点
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
            if "7890" in env_copy.get(k, ""):
                env_copy.pop(k, None)

        if ctx.proxy:
            env_copy["OUTLOOK_PROXIES"] = ctx.proxy
            env_copy["CLOAK_USE_PROXY"] = "1"

        if not ctx.headless:
            env_copy["OUTLOOK_HEADLESS"] = "0"
            env_copy["CLOAK_HEADLESS"] = "0"
        else:
            env_copy["OUTLOOK_HEADLESS"] = "1"
            env_copy["CLOAK_HEADLESS"] = "1"

        accounts_dir = os.path.join(_ROOT, "outlook_accounts")
        prev_mtime = 0
        if os.path.exists(accounts_dir):
            existing_files = [os.path.join(accounts_dir, f) for f in os.listdir(accounts_dir) if f.startswith("accounts_")]
            if existing_files:
                prev_mtime = max(os.path.getmtime(f) for f in existing_files)

        logger.info(f"[Outlook] 启动微软邮箱自动生产流程: {' '.join(cmd)} (Proxy: {ctx.proxy or 'Direct'})")
        proc = subprocess.run(
            cmd,
            cwd=_ROOT,
            capture_output=False,
            text=True,
            env=env_copy,
        )

        registered_email = ""
        refresh_token = ""
        if os.path.exists(accounts_dir):
            txt_files = sorted(
                [os.path.join(accounts_dir, f) for f in os.listdir(accounts_dir) if f.startswith("accounts_") and f.endswith(".txt")],
                key=os.path.getmtime,
                reverse=True,
            )
            if txt_files and os.path.getmtime(txt_files[0]) >= prev_mtime:
                try:
                    with open(txt_files[0], "r", encoding="utf-8") as f:
                        lines = [l.strip() for l in f if l.strip()]
                        if lines:
                            parts = lines[-1].split("----")
                            if len(parts) >= 3 and "@" in parts[0]:
                                registered_email = parts[0]
                                refresh_token = parts[2]
                except Exception:
                    pass

        if proc.returncode == 0 and registered_email:
            return RegistrationResult(
                ok=True,
                email=registered_email,
                platform=self.id,
                refresh_token=refresh_token,
                status="active",
            )
        else:
            return RegistrationResult(
                ok=False,
                email=registered_email or "outlook_failed",
                platform=self.id,
                error=f"Outlook 生产未达预期 (exit_code: {proc.returncode})",
            )
