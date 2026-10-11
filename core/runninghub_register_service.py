# -*- coding: utf-8 -*-
"""
RunningHub (runninghub.ai) 自动化注册服务组件 (基于 BaseRegistrationFlow 模板方法重构)。
深度融合 turb-gpt-free-register 技术栈：
1. 账号源：优先从 uned_accounts / email_pool 中提取未使用的学生邮箱/Outlook/IMAP 邮箱
2. 网络层：绑定固定住宅代理 (Resin 或自定义代理出口)，确保单号固定 IP
3. 交互层：使用 Playwright + stealth 模拟真人交互，自动化进入注册流程
4. 验证码：自动监听并读取邮箱验证码 (通过 Microsoft Graph API 或通用 IMAP 协议)
5. 礼包码：自动填入邀请码 (获得 1000 算力币新手礼包)
6. 自动入库：注册成功后提取 Rh-Accesstoken 并调用 runninghub2api_sync 推送至网关
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import secrets
import string
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from playwright.async_api import async_playwright
import playwright_stealth

from core.flows.base import BaseRegistrationFlow
from core.flows.context import FlowContext, RegistrationResult as FlowRegistrationResult
from core.flows.pool_helper import pick_unregistered_email_for_platform
from .otp_utils import extract_otp
from .runninghub2api_sync import push_account_to_rh2api

logger = logging.getLogger("runninghub.register")

RUNNINGHUB_URL = "https://www.runninghub.ai"
DEFAULT_INVITE_CODE = "rh-v1180"


def generate_strong_password(length: int = 12) -> str:
    """生成符合 RunningHub 要求的密码（纯大小写英文字母+数字，8~16位，禁止特殊字符，CSPRNG）。"""
    upper = secrets.choice(string.ascii_uppercase)
    lower = secrets.choice(string.ascii_lowercase)
    digit = secrets.choice(string.digits)
    all_chars = string.ascii_letters + string.digits
    rest = "".join(secrets.choice(all_chars) for _ in range(length - 3))
    pwd_list = list(upper + lower + digit + rest)
    secrets.SystemRandom().shuffle(pwd_list)
    return "".join(pwd_list)


def format_playwright_proxy(proxy_url: Optional[str]) -> Optional[Dict[str, str]]:
    if not proxy_url or not str(proxy_url).strip():
        return None
    from urllib.parse import urlsplit
    u = urlsplit(str(proxy_url).strip())
    cfg = {"server": f"{u.scheme}://{u.hostname}:{u.port}"}
    if u.username:
        cfg["username"] = u.username
    if u.password:
        cfg["password"] = u.password
    return cfg


@dataclass
class RegistrationResult:
    """向后兼容原有 RunningHub 返回结构。"""
    success: bool
    email: str
    password: str = ""
    token: str = ""
    user_id: str = ""
    proxy: str = ""
    total_coin: int = 0
    error: str = ""


class RunningHubRegistrar:
    def __init__(self, invite_code: str = DEFAULT_INVITE_CODE, debug: bool = False):
        self.invite_code = invite_code
        self.debug = debug
        if debug:
            logging.basicConfig(level=logging.DEBUG)
        else:
            logging.basicConfig(level=logging.INFO)

    def fetch_email_otp(self, email: str, wait_timeout: int = 120, poll_interval: int = 4) -> Optional[str]:
        """根据邮箱类型，自动调度 Outlook Client 或通用 IMAP Client 轮询提取 6 位验证码。"""
        logger.info(f"[{email}] 正在等待 RunningHub 注册验证码邮件...")
        started = time.time()

        is_outlook = any(dom in email.lower() for dom in ("outlook.com", "hotmail.com", "live.com", "uned.es"))

        while time.time() - started < wait_timeout:
            try:
                # 通道 A: Microsoft Graph API / Outlook REST
                if is_outlook:
                    try:
                        from .outlook_client import (
                            get_account_context,
                            _ms_access_token,
                            _ms_http,
                            _fetch_graph_messages,
                            _fetch_outlook_rest_messages
                        )
                        ctx = get_account_context(email)
                        if ctx:
                            http_c = _ms_http()
                            token, kind = _ms_access_token(ctx, http=http_c)
                            msgs = _fetch_graph_messages(http_c, token) if kind == "graph" else _fetch_outlook_rest_messages(http_c, token)
                            for m in msgs:
                                body_all = f"{m.get('subject', '')} {m.get('body', '')} {m.get('bodyPreview', '')}".lower()
                                if any(k in body_all for k in ("runninghub", "running hub", "verification", "código", "code")):
                                    otp = extract_otp(m) or extract_otp(m.get("body", "") or m.get("content", ""))
                                    if otp:
                                        logger.info(f"[{email}] 成功从 Outlook/Graph 提取到验证码: {otp}")
                                        return otp
                    except Exception as e_ms:
                        logger.debug(f"Outlook 读取跳过: {e_ms}")

                # 通道 B: 通用 IMAP 协议回退
                try:
                    from .imap_mail_client import get_account_context as get_imap_ctx, _fetch_inbox_messages
                    imap_ctx = get_imap_ctx(email)
                    if imap_ctx:
                        msgs = _fetch_inbox_messages(imap_ctx, limit=5)
                        for m in msgs:
                            body_all = f"{m.get('subject', '')} {m.get('body', '')}".lower()
                            if any(k in body_all for k in ("runninghub", "code", "verification")):
                                otp = extract_otp(m) or extract_otp(m.get("body", ""))
                                if otp:
                                    logger.info(f"[{email}] 成功从 IMAP 提取到验证码: {otp}")
                                    return otp
                except Exception as e_imap:
                    logger.debug(f"IMAP 读取跳过: {e_imap}")

            except Exception as e:
                logger.error(f"读取邮件异常: {e}")

            time.sleep(poll_interval)

        logger.warning(f"[{email}] 等待邮件验证码超时 ({wait_timeout}s)")
        return None

    async def register_one_async(
        self,
        email: str,
        password: Optional[str] = None,
        proxy_url: Optional[str] = None,
        headless: bool = False,
        timeout_sec: int = 180,
    ) -> RegistrationResult:
        """基于 Playwright 自动化完成单个账号注册并提取 Token。"""
        password = password or generate_strong_password()
        proxy_cfg = format_playwright_proxy(proxy_url)

        logger.info(f"[{email}] 启动浏览器准备注册 (代理: {proxy_url or '直连'}, 模式: {'静默' if headless else '界面'})")

        async with async_playwright() as p:
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-infobars",
            ]
            browser = await p.chromium.launch(
                headless=headless,
                proxy=proxy_cfg,
                args=launch_args,
            )
            context = await browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            )
            page = await context.new_page()
            try:
                from core.live_browser_hub import LiveBrowserHub
                LiveBrowserHub.get_hub().attach_page(page)
            except Exception as e_live:
                logger.debug(f"LiveBrowser attach: {e_live}")

            try:
                from playwright_stealth import Stealth
                await Stealth().apply_stealth_async(page)
            except Exception as e_st:
                logger.debug(f"Stealth apply: {e_st}")

            try:
                # 1. 访问首页并打开注册弹窗
                logger.info(f"[{email}] 正在加载 RunningHub 首页...")
                await page.goto(RUNNINGHUB_URL, timeout=45000, wait_until="networkidle")
                await page.wait_for_timeout(2000)

                login_btn = page.locator("button.login-btn, button:has-text('Sign In')").first
                await login_btn.wait_for(state="visible", timeout=20000)

                for _ in range(3):
                    await login_btn.click(force=True)
                    try:
                        await page.locator(".ant-modal").first.wait_for(state="visible", timeout=4000)
                        break
                    except Exception:
                        await page.wait_for_timeout(1000)

                await page.wait_for_timeout(1500)

                # 切换到 Email Login
                email_tab = page.locator(".ant-modal").get_by_text(re.compile(r"Email Login|邮箱登录", re.I)).first
                await email_tab.wait_for(state="visible", timeout=15000)
                await email_tab.click(force=True)
                await page.wait_for_timeout(1800)

                # 切换到 SIGN UP 注册标签
                signup_tab = page.locator(".ant-modal").get_by_text(re.compile(r"SIGN UP|注册", re.I)).first
                await signup_tab.wait_for(state="visible", timeout=15000)
                await signup_tab.click(force=True)
                await page.wait_for_timeout(1800)

                # 2. 填写 Step 1：邮箱与密码
                logger.info(f"[{email}] 填写注册表单 (Email & Password)...")
                await page.locator(".ant-modal input").first.wait_for(state="visible", timeout=15000)

                email_input = page.locator(".ant-modal input:not([type='password'])").first
                pwd_input = page.locator(".ant-modal input[type='password']").nth(0)
                confirm_input = page.locator(".ant-modal input[type='password']").nth(1)

                await email_input.fill(email)
                await page.wait_for_timeout(400)
                await pwd_input.fill(password)
                await page.wait_for_timeout(400)
                await confirm_input.fill(password)
                await page.wait_for_timeout(800)

                # 点击 Next Step
                next_btn = page.locator(".ant-modal button:has-text('Next Step'), .ant-modal button:has-text('下一步')").first
                await next_btn.wait_for(state="visible", timeout=10000)
                await next_btn.click()
                logger.info(f"[{email}] 点击 Next Step...")
                await page.wait_for_timeout(2500)

                # 3. 检查旋转验证码并等待进入 Step 2
                try:
                    from core.live_browser_hub import LiveBrowserHub
                    LiveBrowserHub.ensure_server(8799)
                    LiveBrowserHub.open_browser(8799)
                except Exception as _ex:
                    logger.debug(f"LiveBrowser auto open: {_ex}")

                logger.info(f"[{email}] 正在等待滑块/旋转拼图验证通过 (已在浏览器打开 http://127.0.0.1:8799/live，请直接在网页中拖动滑块通过验证)...")
                step2_ready = False
                for wait_i in range(1, 301):
                    # 如果找到了验证码输入框，说明滑块已通过并进入了 Step 2
                    code_el = await page.query_selector("input[placeholder*='code' i], input[placeholder*='verification' i], input[placeholder*='验证码' i], .verification-code-input")
                    if code_el and await code_el.is_visible():
                        step2_ready = True
                        logger.info(f"[{email}] [SUCCESS] 滑块拼图已通过，成功进入 Step 2 (邮箱验证码输入界面)！")
                        break
                    if wait_i % 10 == 0:
                        logger.info(f"[{email}] 正在等待滑块验证通过... (已等待 {wait_i}s / 300s，可在 http://127.0.0.1:8799/live 中拖动)")
                    await page.wait_for_timeout(1000)

                if not step2_ready:
                    logger.warning(f"[{email}] 尚未检测到 Step 2 输入框，尝试继续向下执行...")

                # 4. 获取邮箱验证码并填写 Step 2
                otp = self.fetch_email_otp(email, wait_timeout=120)

                if not otp:
                    raise RuntimeError("未能在有效时间内获取到邮箱验证码")

                logger.info(f"[{email}] 正在填入验证码: {otp}")
                await page.wait_for_timeout(1000)

                code_input = await page.wait_for_selector(
                    "input[placeholder*='code' i], input[placeholder*='verification' i], input[placeholder*='code']",
                    timeout=15000
                )
                await code_input.fill(otp)
                await page.wait_for_timeout(500)

                # 5. 填写邀请码 (拿 1000 币奖励)
                if self.invite_code:
                    invite_input = await page.query_selector("input[placeholder*='invite' i], input[placeholder*='referral' i]")
                    if invite_input:
                        logger.info(f"[{email}] 填入邀请码: {self.invite_code}")
                        await invite_input.fill(self.invite_code)
                        await page.wait_for_timeout(400)

                # 点击完成注册按钮
                submit_btn = await page.wait_for_selector(
                    "button.ant-btn-primary:not(.login-btn), button:has-text('Sign Up'), button:has-text('Register'), button:has-text('Complete'), button:has-text('Confirm')",
                    timeout=10000
                )
                await submit_btn.click()
                logger.info(f"[{email}] 提交注册信息，等待注册完成与登录态写入...")
                await page.wait_for_timeout(6000)

                # 6. 从浏览器 localStorage / Cookies 提取鉴权信息
                storage = await page.evaluate("() => ({ ...localStorage })")
                cookies = await context.cookies()

                token = ""
                user_id = ""
                for k in ("Rh-Accesstoken", "rh-accesstoken", "token", "access_token"):
                    if k in storage and storage[k]:
                        token = storage[k]
                        break
                if not token:
                    for c in cookies:
                        if c["name"] in ("Rh-Accesstoken", "rh-accesstoken", "token"):
                            token = c["value"]
                            break

                if "userInfo" in storage:
                    try:
                        uinfo = json.loads(storage["userInfo"])
                        user_id = str(uinfo.get("id") or uinfo.get("userId") or "")
                    except Exception:
                        pass

                if not token:
                    uinfo_eval = await page.evaluate("""async () => {
                        try {
                            const res = await fetch('/uc/getUserInfo', { credentials: 'include' });
                            return await res.json();
                        } catch(e) { return null; }
                    }""")
                    if uinfo_eval and uinfo_eval.get("data"):
                        user_id = str(uinfo_eval["data"].get("userId") or uinfo_eval["data"].get("id") or "")

                if not token:
                    raise RuntimeError("注册提交后未能在浏览器环境中捕获到有效 Rh-Accesstoken")

                logger.info(f"[SUCCESS] 注册成功！用户ID: {user_id or '未命名'}, Token: {token[:12]}...")

                # 7. 自动推送入库到 RunningHub2API 网关
                push_ok = push_account_to_rh2api(
                    token=token,
                    user_id=user_id or email,
                    email=email,
                    proxy=proxy_url or "",
                    total_coin=1000,
                    remark=f"UNED自动化注册 (邀请码: {self.invite_code})",
                )

                try:
                    from core.live_browser_hub import LiveBrowserHub
                    LiveBrowserHub.get_hub().detach_page()
                except Exception:
                    pass
                await browser.close()
                return RegistrationResult(
                    success=True,
                    email=email,
                    password=password,
                    token=token,
                    user_id=user_id,
                    proxy=proxy_url or "",
                    total_coin=1000,
                )

            except Exception as e:
                logger.error(f"[{email}] 注册流程异常: {e}")
                try:
                    from core.live_browser_hub import LiveBrowserHub
                    LiveBrowserHub.get_hub().detach_page()
                except Exception:
                    pass
                await browser.close()
                return RegistrationResult(
                    success=False,
                    email=email,
                    error=str(e),
                )

    def register_one(
        self,
        email: str,
        password: Optional[str] = None,
        proxy_url: Optional[str] = None,
        headless: bool = False,
    ) -> RegistrationResult:
        return asyncio.run(self.register_one_async(
            email=email,
            password=password,
            proxy_url=proxy_url,
            headless=headless,
        ))


class RunningHubRegistrationFlow(BaseRegistrationFlow):
    """RunningHub 注册流水线模板实现。"""

    platform_name = "runninghub"
    platform_table = "runninghub_accounts"

    def build_driver(self, context: FlowContext) -> Any:
        # RunningHub 使用自管理的 Playwright async context，无需 CloakBrowser
        return None

    def execute(self, context: FlowContext) -> FlowRegistrationResult:
        invite_code = context.metadata.get("invite_code", DEFAULT_INVITE_CODE)
        registrar = RunningHubRegistrar(invite_code=invite_code)
        raw_res = registrar.register_one(
            email=context.email,
            password=context.password,
            proxy_url=context.proxy,
            headless=context.headless,
        )

        if raw_res.success:
            return FlowRegistrationResult(
                ok=True,
                email=raw_res.email,
                platform=self.platform_name,
                access_token=raw_res.token,
                credits=raw_res.total_coin,
                metadata={
                    "user_id": raw_res.user_id,
                    "password": raw_res.password,
                    "raw_res": raw_res,
                },
            )
        else:
            return FlowRegistrationResult(
                ok=False,
                email=raw_res.email,
                platform=self.platform_name,
                error=raw_res.error,
            )

    def persist(self, context: FlowContext, result: FlowRegistrationResult) -> bool:
        from .runninghub2api_sync import save_runninghub_account_to_db
        raw_res = result.metadata.get("raw_res")
        pwd = result.metadata.get("password") or getattr(raw_res, "password", "")
        uid = result.metadata.get("user_id") or getattr(raw_res, "user_id", "")
        invite = context.metadata.get("invite_code") or DEFAULT_INVITE_CODE
        return save_runninghub_account_to_db(
            email=result.email,
            token=result.access_token,
            password=pwd,
            user_id=uid,
            credits=result.credits or 1000,
            invite_code=invite,
            cookies=result.cookies,
            proxy_used=context.proxy or "",
            payload=context.extra_payload,
            db_path=context.db_path,
        )

    def push(self, context: FlowContext, result: FlowRegistrationResult) -> bool:
        user_id = result.metadata.get("user_id") or result.email
        pushed = push_account_to_rh2api(
            token=result.access_token,
            user_id=user_id,
            email=result.email,
            proxy=context.proxy or "",
            total_coin=result.credits,
            remark=f"UNED自动化注册 (流水线产出)",
        )
        result.synced_to_gateway = pushed
        return pushed


def run_runninghub_registration(
    email: str,
    password: Optional[str] = None,
    proxy: Optional[str] = None,
    headless: bool = False,
    invite_code: str = DEFAULT_INVITE_CODE,
    db_path: Path | str | None = None,
) -> FlowRegistrationResult:
    """高阶统一流水线入口。"""
    flow = RunningHubRegistrationFlow(db_path=db_path)
    return flow.run(
        account_info={"email": email, "payload": {}},
        proxy=proxy,
        headless=headless,
        invite_code=invite_code,
    )
