# -*- coding: utf-8 -*-
"""OpenAI 专属独立密码设定与对齐服务。

实现基于官方邮件密码重置（Forgot Password）机制的端到端闭环：
1. 在指纹浏览器上下文中发起密码重置请求；
2. 毫秒级轮询 Outlook/微软邮箱收取重置安全链接；
3. 浏览器跳转进入重置链接并填入 16 位强随机专属密码；
4. 提交确认并自动写入 SQLite 数据库（accounts）与下游 gptGrok2api。
"""
from __future__ import annotations

import logging
import random
import secrets
import string
import time
from typing import Any

from core import db
from core.email_provider import wait_for_password_reset_link

logger = logging.getLogger(__name__)


def generate_secure_password(length: int = 16) -> str:
    """生成满足 OpenAI 安全规范的 16 位专属强密码（含大写字母、小写字母、数字、特殊符号）。"""
    upper = "ABCDEFGHJKLMNPQRSTUVWXYZ"
    lower = "abcdefghjkmnpqrstuvwxyz"
    digits = "23456789"
    symbols = "!@#$%^&*?_-+="
    groups = [upper, lower, digits, symbols]
    all_chars = "".join(groups)

    # 保证每种字符至少出现一次
    pwd_chars = [secrets.choice(g) for g in groups]
    for _ in range(max(0, length - len(groups))):
        pwd_chars.append(secrets.choice(all_chars))
    secrets.SystemRandom().shuffle(pwd_chars)
    return "".join(pwd_chars)


def _guard_browser(page: Any) -> None:
    """微型防盾/网络异常守卫。"""
    try:
        url = getattr(page, "url", "") or ""
        if "chrome-error://" in url:
            page.reload(wait_until="domcontentloaded", timeout=15000)
    except Exception:
        pass


def request_password_reset_email(page: Any, email: str) -> bool:
    """在当前浏览器上下文发起 Forgot Password 请求，使 OpenAI 向邮箱投递重置链接。"""
    try:
        logger.info("[密码对齐] 正在通过浏览器发起 Forgot Password 请求: %s", email)
        # 1. 优先尝试点击页面现有的 Forgot password 链接
        clicked = False
        try:
            forgot_loc = page.locator(
                "a:has-text('Forgot password?'), a:has-text('Forgot password'), "
                "button:has-text('Forgot password'), a:has-text('忘记密码'), "
                "a[href*='forgot-password'], a[href*='reset']"
            ).first
            if forgot_loc.is_visible(timeout=1500):
                forgot_loc.click()
                clicked = True
        except Exception:
            pass

        if not clicked:
            try:
                page.goto("https://auth.openai.com/forgot-password", wait_until="domcontentloaded", timeout=20000)
            except Exception as nav_err:
                logger.debug("[密码对齐] 导航 forgot-password 提示: %s", nav_err)

        time.sleep(2.0)
        _guard_browser(page)

        # 2. 填写邮箱
        email_loc = page.locator("input[type='email'], input[name='email'], input[name='username'], input#email-input").first
        if email_loc.is_visible(timeout=5000):
            email_loc.click()
            try:
                page.keyboard.press("Control+A")
                page.keyboard.press("Backspace")
            except Exception:
                pass
            page.keyboard.type(email, delay=25)
            time.sleep(0.5)

        # 3. 点击提交继续按钮
        submit_btn = page.locator(
            "button:has-text('Continue'), button:has-text('Reset password'), button:has-text('继续'), button[type='submit']"
        ).first
        if submit_btn.is_visible(timeout=4000):
            submit_btn.click()
            time.sleep(2.5)
            _guard_browser(page)
            logger.info("[密码对齐] Forgot Password 请求已成功提交: %s", email)
            return True
        return False
    except Exception as exc:
        logger.warning("[密码对齐] 发起 Forgot Password 请求异常: %s", exc)
        return False


def submit_new_password_form(page: Any, reset_url: str, new_password: str) -> bool:
    """进入密码重置链接并在页面填入新密码完成确认。"""
    try:
        logger.info("[密码对齐] 正在进入密码重置链接并填入新密码...")
        page.goto(reset_url, wait_until="domcontentloaded", timeout=30000)
        time.sleep(2.5)
        _guard_browser(page)

        # 填入主密码输入框
        pwd_box = page.locator(
            "input[type='password'], input[name='password'], input[name='newPassword'], input#password"
        ).first
        if not pwd_box.is_visible(timeout=8000):
            logger.warning("[密码对齐] 未找到新密码输入框")
            return False

        pwd_box.click()
        page.keyboard.type(new_password, delay=20)
        time.sleep(0.5)

        # 填入密码确认框（若存在）
        confirm_box = page.locator(
            "input[name='confirmPassword'], input[name='password_confirmation'], input[name='confirm_password'], input#confirmPassword"
        ).first
        if confirm_box.is_visible(timeout=1500):
            confirm_box.click()
            page.keyboard.type(new_password, delay=20)
            time.sleep(0.5)

        # 提交新密码
        submit_reset = page.locator(
            "button:has-text('Reset password'), button:has-text('Save'), button:has-text('重置密码'), "
            "button:has-text('Continue'), button:has-text('Save password'), button[type='submit']"
        ).first
        if submit_reset.is_visible(timeout=3000):
            submit_reset.click()
            time.sleep(3.0)
            _guard_browser(page)
            logger.info("[密码对齐] ✅ 密码重设表单已提交完毕！")
            return True
        logger.warning("[密码对齐] 未找到密码重置提交按钮")
        return False
    except Exception as exc:
        logger.error("[密码对齐] 提交新密码表单失败: %s", exc)
        return False


def set_openai_account_password(
    driver_or_page: Any,
    email: str,
    *,
    new_password: str | None = None,
    account_id: int | None = None,
    timeout: int = 50,
) -> str | None:
    """端到端完成 OpenAI 专属密码的自动生成、重置链接收取、页面提交与双向落库同步。

    Args:
        driver_or_page: CloakSeleniumDriver 或 Playwright Page 实例
        email: 目标账号邮箱
        new_password: 指定新密码（默认自动生成 16 位强密码）
        account_id: 可选的数据库账号 ID（若传入则自动持久化并同步 gptGrok2api）
        timeout: 收取邮件最长等待时间（秒）

    Returns:
        成功设定并生效的密码字符串；失败返回 None。
    """
    page = getattr(driver_or_page, "page", None) or driver_or_page
    if not page or not hasattr(page, "goto"):
        logger.error("[密码对齐] 无法从 driver_or_page 获取有效 page 实例")
        return None

    email = str(email or "").strip()
    if not email:
        return None

    final_password = new_password or generate_secure_password(16)
    req_ts = time.time()

    # 1. 触发密码重置邮件
    if not request_password_reset_email(page, email):
        logger.warning("[密码对齐] 触发重置邮件可能未成功，尝试直接轮询收件箱...")

    # 2. 从邮箱中拉取重置安全链接
    try:
        reset_link = wait_for_password_reset_link(email, after_ts=req_ts - 5.0, max_wait=timeout)
    except Exception as mail_err:
        logger.warning("[密码对齐] 收取密码重置邮件超时或异常: %s。已跳过该账号，标记为【需手动对齐密码】", mail_err)
        return None

    # 3. 浏览器进入重置页面并填写设定新密码
    ok = submit_new_password_form(page, reset_link, final_password)
    if not ok:
        return None

    # 4. 如果有 account_id，自动更新 SQLite 与下游 gptGrok2api
    if account_id:
        try:
            db.update_account_registration_password(account_id, final_password)
            logger.info("[密码对齐] 已将新密码回写 SQLite 并推送到 gptGrok2api: acc_id=%d email=%s", account_id, email)
        except Exception as db_err:
            logger.warning("[密码对齐] 回写数据库异常: %s", db_err)

    return final_password
