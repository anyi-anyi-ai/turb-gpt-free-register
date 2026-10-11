# -*- coding: utf-8 -*-
"""
GitHub 账号注册高层业务服务编排层。
集成：
- CloakBrowser 指纹环境构建
- 启发式表单填写与自愈
- Arkose Labs 人机双通道接管
- 邮件验证码自动监听
- GrokIQ 风格初生质量自检探针
- SQLite 规范化持久化
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Dict, Any, Optional

from core.cloakbrowser_driver import build_cloak_driver
from core.fingerprint import derive_account_fingerprint_seed
from core.outlook_client import OutlookAccount

from .credentials import generate_geek_username, generate_strong_password
from .db import save_github_account
from .flow import (
    fill_signup_form,
    wait_for_arkose_and_takeover,
    inject_verification_code,
    skip_onboarding_guide,
)
from .otp import fetch_github_launch_code
from .probe import probe_github_account_quality

logger = logging.getLogger("github.service")


def register_github_account_session(
    account: OutlookAccount,
    proxy_url: str | None,
    proxy_desc: str,
    headless: bool = False,
) -> dict:
    """执行单个 GitHub 账号的有头人机协同注册全生命周期。"""
    email = account.email
    password = generate_strong_password(16)
    fingerprint_seed = derive_account_fingerprint_seed(email)
    signup_start_ts = time.time()

    print("\n" + "─" * 70)
    print("🚀 【启动注册会话】")
    print(f"   • 目标邮箱 : {email}")
    print(f"   • 生成密码 : {password}")
    print(f"   • 出口网络 : {proxy_desc}")
    print(f"   • 指纹环境 : CloakBrowser (Seed: {fingerprint_seed})")
    print("─" * 70)

    # 1. 启动 Cloak 指纹浏览器
    driver, open_result = build_cloak_driver(
        proxy=proxy_url,
        headless=headless,
        fingerprint_seed=fingerprint_seed,
        account_email=email,
    )

    page = getattr(driver, "page", None)
    if not page:
        driver.quit()
        raise RuntimeError("CloakBrowser 未提供有效的 Page 实例")

    try:
        # 2. 导航进入注册页
        logger.info("🌐 [导航] 正在打开 GitHub 注册入口: https://github.com/signup ...")
        page.goto("https://github.com/signup", wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)

        # 3. 填表推进 (支持用户名占用自动重试)
        username = ""
        form_ok = False
        for attempt in range(5):
            username = generate_geek_username(email, attempt=attempt)
            res_form = fill_signup_form(page, email, password, username)
            status = res_form.get("status")

            if status == "email_taken":
                logger.warning(f"⚠️ 邮箱 {email} 已在 GitHub 注册过，已标记归档！")
                save_github_account(
                    username="github-existing",
                    email=email,
                    password=account.password,
                    email_password=account.password,
                    proxy_used=proxy_desc,
                    fingerprint_seed=fingerprint_seed,
                    status="already_registered",
                    extra={"reason": "email_taken_on_github"}
                )
                return {"status": "already_registered", "email": email}

            if status == "username_taken":
                logger.info(f"   -> 用户名 {username} 已被占用，正在重试新命名...")
                # 清空用户名输入框准备下一次重试
                try:
                    user_loc = page.locator("input#login, input[name='user[login]']").first
                    user_loc.click()
                    page.keyboard.press("Control+A")
                    page.keyboard.press("Backspace")
                except Exception:
                    pass
                continue

            form_ok = True
            break

        if not form_ok:
            logger.error("❌ 连续 5 次尝试生成用户名均被占用或表单异常")
            return {"status": "username_exhausted", "email": email}

        # 4. 【人机协同】Arkose Labs 验证
        takeover = wait_for_arkose_and_takeover(page, max_wait=360)
        if not takeover:
            logger.warning("⚠️ 等待人工验证超时，请确认是否已完成挑战")
            try:
                user_confirm = input(">>> 请输入 c 继续接管，或按任意键取消: ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                user_confirm = "c"
            if user_confirm != "c":
                return {"status": "timeout", "email": email}

        # 5. 自动接管：收取并输入邮箱验证码
        logger.info("⚡ [自动化接管] 开始收取 GitHub Launch Code 验证码...")
        otp_code = fetch_github_launch_code(account, after_ts=signup_start_ts, max_wait=180)

        if not otp_code:
            print(f"\n⚠️ 自动收信未提取到验证码，若您已在邮箱中看到，可在此处手动输入：")
            try:
                manual_code = input(">>> 请输入 6 位/8 位验证码 (直接回车跳过): ").strip()
            except (EOFError, KeyboardInterrupt):
                manual_code = ""
            if manual_code and manual_code.isdigit():
                otp_code = manual_code

        if not otp_code:
            return {"status": "otp_failed", "email": email}

        inject_verification_code(page, otp_code)

        # 6. 跳过 Onboarding 引导
        skip_onboarding_guide(page)

        # 7. 提取 Cookie 会话
        cookies = driver.context.cookies()
        user_session = next((c["value"] for c in cookies if c.get("name") == "user_session"), None)
        dotcom_user = next((c["value"] for c in cookies if c.get("name") == "dotcom_user"), None)

        # 8. 借鉴 GrokIQ：发起初生质量自检探针 (Post-Registration Probe)
        quality_status, probe_info = probe_github_account_quality(
            username=username,
            cookies=cookies,
            proxy=proxy_url,
        )

        logger.info(f"🎉🎉🎉 【注册结果】账号 {username} ({email}) 完成认证！初生质量评级: 【{quality_status}】")

        # 9. 持久化存储
        save_github_account(
            username=username,
            email=email,
            password=password,
            email_password=account.password,
            cookies=cookies,
            proxy_used=proxy_desc,
            fingerprint_seed=fingerprint_seed,
            status=quality_status,
            extra={
                "dotcom_user": dotcom_user or username,
                "probe_info": probe_info,
                "registered_via": "cloak_hitl_decoupled",
                "registered_at": datetime.now(timezone.utc).isoformat(),
            }
        )

        return {
            "status": "success",
            "quality": quality_status,
            "username": username,
            "email": email,
            "password": password,
            "email_password": account.password,
            "dotcom_user": dotcom_user or username,
            "proxy": proxy_desc,
        }

    except Exception as exc:
        logger.error(f"❌ 注册流水线异常: {exc}", exc_info=True)
        return {"status": "error", "error": str(exc), "email": email}

    finally:
        try:
            driver.quit()
        except Exception:
            pass
