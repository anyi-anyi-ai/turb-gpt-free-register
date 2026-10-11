# -*- coding: utf-8 -*-
"""
Suno (suno.com) 自动化注册服务模块 (基于 BaseRegistrationFlow 模板方法重构)。
架构层级：
  - 驱动层：基于 CloakBrowser 指纹浏览器 + 纯净出口代理，规避浏览器指纹风控
  - 协议与授权层：打通 Microsoft OAuth 链路，自动化完成 Live/MicrosoftOnline 登录与权限授权
  - 人机穿透层：深度整合 cloudflare_turnstile 模块处理 Suno 页面与会话建立时的 Turnstile 质询
  - 凭据沉淀与解耦层：提取核心 Clerk/Suno 会话凭证 (__client, sessionid, cookies)，持久化至 SQLite 并推送至下游网关
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

from core.flows.base import BaseRegistrationFlow
from core.flows.context import FlowContext, RegistrationResult
from core.flows.pool_helper import pick_unregistered_email_for_platform
from core.cloudflare_turnstile import solve_or_wait_cloudflare_turnstile, is_cloudflare_challenge
from core.suno2api_sync import (
    init_suno_db,
    save_suno_account_to_db,
    push_account_to_suno2api,
    check_suno2api_health,
)
from core.name_samples import random_display_name
from core.profile_utils import generate_random_birthday

logger = logging.getLogger("suno.register")

_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _ROOT / "turb.sqlite3"


class SunoRegistrationFlow(BaseRegistrationFlow):
    """Suno 自动化注册流水线实现。"""

    platform_name = "suno"
    platform_table = "suno_accounts"

    def execute(self, context: FlowContext) -> RegistrationResult:
        driver = context.driver
        email = context.email
        pwd = context.password or context.extra_payload.get("password", "")
        full_name = context.metadata.get("full_name") or random_display_name()

        logger.info("[SunoReg] 正在打开 Suno 注册页 (https://suno.com/sign-up)...")
        driver.get("https://suno.com/sign-up")
        time.sleep(3.0)

        # 检查是否遇到前置 Cloudflare 盾
        if is_cloudflare_challenge(driver):
            logger.info("[SunoReg] 触发前置 Cloudflare 质询，开始自动穿透...")
            solve_or_wait_cloudflare_turnstile(driver, timeout=30.0)
            time.sleep(2.0)

        # 点击 Continue with Microsoft
        logger.info("[SunoReg] 定位并点击 'Continue with Microsoft' 按钮...")
        ms_btn = driver.page.locator("button:has-text('Continue with Microsoft')").first
        ms_btn.wait_for(timeout=15000)
        ms_btn.click(force=True)

        # 等待 Microsoft 登录表单
        logger.info("[SunoReg] 等待 Microsoft 登录页面加载...")
        driver.page.wait_for_selector("input[name='loginfmt']", timeout=25000)
        logger.info(f"[SunoReg] 填入微软账号: {email}")
        driver.page.locator("input[name='loginfmt']").fill(email)
        time.sleep(1.0)
        driver.page.locator("input[name='loginfmt']").press("Enter")
        time.sleep(5.0)

        # 检查是否提示切换为密码登录
        use_pwd = driver.page.locator("text='Use your password'")
        if use_pwd.count() > 0 and use_pwd.is_visible():
            logger.info("[SunoReg] 检测到密码登录切换入口，点击 'Use your password'...")
            use_pwd.click()
            time.sleep(3.0)

        # 输入密码
        pwd_inp = driver.page.locator("input[name='passwd'], input[type='password']").first
        if pwd_inp.count() > 0:
            logger.info("[SunoReg] 填入密码并提交...")
            pwd_inp.fill(pwd)
            time.sleep(1.0)
            pwd_inp.press("Enter")
            time.sleep(5.0)

        # 检查是否有条款确认按钮 (primaryButton / Next)
        btn = driver.page.locator("button[data-testid='primaryButton'], button:has-text('Next')").first
        if btn.count() > 0 and btn.is_visible():
            logger.info("[SunoReg] 确认微软条款更新 (点击 Next)...")
            btn.click()
            time.sleep(5.0)

        # 处理保持登录 / 授权许可对话框
        for i in range(5):
            cur_url = driver.page.url
            if "suno.com" in cur_url:
                logger.info(f"[SunoReg] 已重定向回 Suno 域名: {cur_url}")
                break
            act_btn = driver.page.locator(
                "button[data-testid='primaryButton'], input[value*='Accept' i], input[value*='Yes' i], "
                "button:has-text('Accept'), button:has-text('Yes'), #idSIButton9"
            ).first
            if act_btn.count() > 0 and act_btn.is_visible():
                logger.info(f"[SunoReg] 点击确认/保持登录按钮: {act_btn.inner_text() or act_btn.get_attribute('value')}")
                act_btn.click()
                time.sleep(5.0)
            else:
                time.sleep(2.0)

        # 若在 auth/verify 页面，执行 Turnstile 自动穿透
        if "auth/verify" in driver.page.url:
            logger.info("[SunoReg] 进入 Suno 认证与合成器预热页 (auth/verify)，检测 Turnstile 质询...")
            solve_or_wait_cloudflare_turnstile(driver, timeout=25.0)

        # 等待会话完成
        for _ in range(15):
            time.sleep(1.0)
            if "auth/verify" not in driver.page.url:
                break

        # 处理生日收集页面（如遇到）
        if "auth/birthday" in driver.page.url:
            logger.info("[SunoReg] 遇到生日设置流程，自动选择日期...")
            try:
                m_btn = driver.page.locator("button:has-text('Month'), div:has-text('Month')").last
                if m_btn.count() > 0:
                    m_btn.click()
                    time.sleep(0.5)
                    driver.page.locator("[role='option'], li:has-text('January'), div:has-text('January')").first.click()
                    time.sleep(0.5)
                d_btn = driver.page.locator("button:has-text('Day'), div:has-text('Day')").last
                if d_btn.count() > 0:
                    d_btn.click()
                    time.sleep(0.5)
                    driver.page.locator("[role='option'], li:has-text('15'), div:has-text('15')").first.click()
                    time.sleep(0.5)
                y_btn = driver.page.locator("button:has-text('Year'), div:has-text('Year')").last
                if y_btn.count() > 0:
                    y_btn.click()
                    time.sleep(0.5)
                    driver.page.locator("[role='option'], li:has-text('1998'), div:has-text('1998')").first.click()
                    time.sleep(0.5)
                cont_btn = driver.page.locator("button:has-text('Continue')").first
                if cont_btn.count() > 0:
                    cont_btn.click()
                    time.sleep(5.0)
            except Exception as e:
                logger.warning(f"[SunoReg] 生日设置步骤跳过或异常: {e}")

        time.sleep(3.0)

        # 收集会话 Cookies
        all_cookies = driver.page.context.cookies()
        cookies_dict: Dict[str, str] = {c["name"]: c["value"] for c in all_cookies}

        clerk_client_id = cookies_dict.get("__client", "")
        session_jwt = cookies_dict.get("__session", "")
        session_id = cookies_dict.get("sessionid", "")

        logger.info(f"[SunoReg] 抓取完成: 共 {len(cookies_dict)} 个 Cookie")
        logger.info(f"   __client 存在: {bool(clerk_client_id)} | sessionid: {bool(session_id)} | __session: {bool(session_jwt)}")

        if not clerk_client_id and not session_id and len(cookies_dict) < 5:
            raise RuntimeError(f"未能提取到有效的 Suno/Clerk 会话 Cookie！当前抓取 Cookie: {list(cookies_dict.keys())}")

        return RegistrationResult(
            ok=True,
            email=email,
            platform=self.platform_name,
            access_token=session_jwt,
            cookies=cookies_dict,
            credits=50,
            status="active",
            metadata={
                "clerk_client_id": clerk_client_id,
                "session_jwt": session_jwt,
                "final_url": driver.page.url,
            },
        )

    def persist(self, context: FlowContext, result: RegistrationResult) -> bool:
        clerk_client_id = result.metadata.get("clerk_client_id", "")
        session_jwt = result.metadata.get("session_jwt", "")
        return save_suno_account_to_db(
            email=result.email,
            cookies=result.cookies,
            clerk_client_id=clerk_client_id,
            session_jwt=session_jwt,
            proxy_used=context.proxy or "",
            extra_payload={
                "registered_at": datetime.utcnow().isoformat(),
                "source": "microsoft_oauth",
                "final_url": result.metadata.get("final_url", ""),
            },
            db_path=context.db_path,
        )

    def push(self, context: FlowContext, result: RegistrationResult) -> bool:
        clerk_client_id = result.metadata.get("clerk_client_id", "")
        session_jwt = result.metadata.get("session_jwt", "")
        synced = push_account_to_suno2api(
            email=result.email,
            cookies=result.cookies,
            session_jwt=session_jwt,
            clerk_client_id=clerk_client_id,
            db_path=context.db_path,
        )
        result.synced_to_gateway = synced
        return synced


# ============================================================
# 向后兼容公开 API (供 CLI、WebUI 与现有外部脚本调用)
# ============================================================

def pick_available_suno_account(
    specific_email: Optional[str] = None,
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """从本地 SQLite 邮箱池中分配未在 suno_accounts 注册过的可用微软/Outlook 邮箱。"""
    p = Path(db_path or DB_PATH)
    init_suno_db(p)

    acc = pick_unregistered_email_for_platform(
        platform_table="suno_accounts",
        specific_email=specific_email,
        db_path=p,
    )
    payload = acc.get("payload") or {}
    pwd = payload.get("password", "")
    acc["password"] = pwd
    acc["full_name"] = payload.get("full_name") or random_display_name()
    return acc


def run_suno_registration(
    account_info: Optional[Dict[str, Any]] = None,
    proxy: Optional[str] = None,
    country: str = "US",
    headless: bool = True,
    push_to_gateway: bool = True,
    otp_max_wait: int = 80,
    db_path: Path | str | None = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """执行单次 Suno 账号全自动自动化注册流水线（走 Microsoft OAuth + Cloudflare Turnstile 自动穿透）。"""
    init_suno_db(db_path)
    if proxy and proxy.lower() in ("direct", "none", "local"):
        proxy = ""
    elif proxy is None:
        proxy = os.getenv("SUNO_PROXY") or os.getenv("RESIN_PROXY") or ""

    flow = SunoRegistrationFlow(db_path=db_path)
    res = flow.run(
        account_info=account_info,
        proxy=proxy,
        headless=headless,
        auto_push=push_to_gateway,
        country=country,
    )

    return {
        "ok": res.ok,
        "email": res.email,
        "proxy": proxy,
        "cookies": res.cookies,
        "clerk_client_id": res.metadata.get("clerk_client_id", ""),
        "session_jwt": res.metadata.get("session_jwt", ""),
        "synced": res.synced_to_gateway,
        "error": res.error,
    }
