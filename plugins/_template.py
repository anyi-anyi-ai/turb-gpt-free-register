# -*- coding: utf-8 -*-
"""
plugins/_template.py — 新网站注册插件标准开发模板 (开发参考)

开发步骤：
1. 复制本文件并重命名为目标平台标识，如 `plugins/my_new_ai.py`
2. 修改类名与元数据 (`id`, `name`, `target_url`, `table_name`)
3. 在 `execute` 方法中编写该网站特有的几个步骤（打开页面、拟人填表、调 kits 提取验证码/链接、返回 Token）
4. 保存后自动被 PluginRegistry 发现并纳入统一调度，无需修改任何已有代码！
"""
from __future__ import annotations

import logging
from typing import Any, Dict

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle
from core.plugins.base import BaseSitePlugin
from core.plugins.registry import register_plugin

logger = logging.getLogger("turb.plugin.template")


# 取消注释即可注册成为正式插件:
# @register_plugin
class MyNewSitePlugin(BaseSitePlugin):
    """新网站注册插件示例。"""

    id = "my_new_ai"                         # 唯一的插件 ID，命令行中运行 `python turb.py run my_new_ai`
    name = "My New AI Platform"              # 平台展示名称
    description = "新平台注册说明..."       # 描述信息
    target_url = "https://example.com/signup" # 注册页面地址
    table_name = "example_accounts"          # SQLite 中存储已注册账号的表名
    browser_type = "cloak"                   # "cloak" (强抗盾) 或 "patchright" (隔离上下文)
    requires_human = False                   # 若需要人工极速拼图协作，设为 True 会自动弹出有头窗口
    default_headless = True                  # 默认是否无头

    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        """
        核心交互逻辑（仅需编写差异化的网页交互与元素定位）：
        kits 中已自动注入 6 大积木：
          - kits.browser: 驱动生命周期与页面控制
          - kits.human: 拟人击键 (kits.human.type_text) 与微颤点击 (kits.human.click_element)
          - kits.email: 自动收码 (kits.email.fetch_verification_code) 或链接 (kits.email.fetch_verification_link)
          - kits.captcha: Turnstile 穿透 (kits.captcha.handle_turnstile)
          - kits.asset: 凭据持久化
          - kits.sms: 智能接码 (with kits.sms.session(service="...", max_retries_per_country=5) as sms)
        """
        driver = ctx.driver
        email = ctx.email
        password = ctx.password

        # 1. 打开页面
        driver.get(self.target_url)

        # 2. 如果遇到 Cloudflare 盾，自动穿透
        kits.captcha.handle_turnstile(driver)

        # 3. 拟人填入邮箱与密码
        page = driver.page
        email_input = page.locator("input[type='email']").first
        kits.human.type_text(email_input, email)

        submit_btn = page.locator("button:has-text('Sign up'), button:has-text('注册')").first
        kits.human.click_element(submit_btn)

        # 4. 自动通过 Graph API 收取 6 位验证码 (无需手动编写长篇循环)
        code = kits.email.fetch_verification_code(email, code_pattern=r"\b(\d{6})\b")
        code_input = page.locator("input[name='code'], input[placeholder*='code']").first
        kits.human.type_text(code_input, code)

        # 5. 提取登录凭证（Token / Cookies / API Key）
        session_token = page.evaluate("() => localStorage.getItem('token') || ''")
        cookies = {c["name"]: c["value"] for c in page.context.cookies()}

        # 6. 返回规范的结果对象（底层基类会自动入库并同步下游）
        return RegistrationResult(
            ok=True,
            email=email,
            platform=self.id,
            access_token=session_token,
            cookies=cookies,
            credits=100,
            status="active",
        )
