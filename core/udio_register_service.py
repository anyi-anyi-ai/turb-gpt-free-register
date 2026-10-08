# -*- coding: utf-8 -*-
"""
Udio (udio.com) 全自动注册服务模块 (基于 BaseRegistrationFlow 模板方法重构)。
负责：
  - 从本地邮箱池分配未注册 Udio 的 Outlook 邮箱
  - 驱动 CloakBrowser 指纹浏览器打开 Udio 并提交邮箱
  - 通过 Microsoft Graph API 毫秒级轮询收取 Udio Magic Link 邮件
  - 自动访问确认链接、点击 Confirm 确权
  - 自动完成 "Create Your Profile" 档案初始化
  - 提取 Supabase Auth 凭据并落盘推送到 music2api 网关
"""
from __future__ import annotations

import json
import logging
import random
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.flows.base import BaseRegistrationFlow
from core.flows.context import FlowContext, RegistrationResult
from core.flows.pool_helper import pick_unregistered_email_for_platform
from core.outlook_client import get_account_context, _http_session, _fetch_via
from core.udio2api_sync import (
    DEFAULT_DB_PATH,
    init_udio_db,
    save_udio_account_to_db,
    push_account_to_music2api,
)

logger = logging.getLogger("udio.register")
DB_PATH = DEFAULT_DB_PATH


def _generate_random_username() -> str:
    """生成合规的 Udio 用户名。"""
    adjectives = ["Cosmic", "Astral", "Sonic", "Velvet", "Echo", "Harmonic", "Solar", "Lunar", "Cyber", "Stellar"]
    nouns = ["Melody", "Wave", "Pulse", "Vibe", "Tune", "Beat", "Rhythm", "Synth", "Audio", "Echo"]
    num = random.randint(1000, 9999)
    return f"{random.choice(adjectives)}{random.choice(nouns)}{num}"


def fetch_latest_udio_magic_link(
    email: str,
    after_ts: float | None = None,
    max_wait: int = 60,
    poll_interval: int = 3,
) -> str:
    """通过 Graph API 轮询收件箱并抽取 Udio Magic Link 确认链接。"""
    account = get_account_context(email)
    if account is None:
        raise RuntimeError(f"未找到 {email} 的账号上下文（请确认 refresh_token 是否存在）")

    deadline = time.time() + max_wait
    session = _http_session()

    logger.info(f"[Outlook] 开始轮询 {email} 的 Udio 确认邮件，最长等待 {max_wait}s...")

    while time.time() < deadline:
        emails = _fetch_via(session, "graph", account)
        for item in emails:
            subj = str(item.get("subject") or "").lower()
            sender = str(item.get("from") or "").lower()
            sender_name = str(item.get("fromName") or "").lower()
            body_txt = str(item.get("bodyText") or "")
            body_html = str(item.get("bodyHtml") or "")
            full_body = f"{body_txt}\n{body_html}"

            # 匹配 Udio 确认邮件
            if any(k in subj for k in ("confirm your signup", "confirm", "udio", "magic link")) or \
               any(k in sender or k in sender_name for k in ("udio", "noreply@udio")):
                matches = re.findall(r'https://[^\s"\'<>]*udio\.com/login/confirm[^\s"\'<>]+', full_body)
                if matches:
                    clean_link = matches[0].replace("&amp;", "&")
                    logger.info(f"[Outlook] 成功提取到 Udio Magic Link: {clean_link}")
                    return clean_link

        time.sleep(poll_interval)

    raise TimeoutError(f"等待 {email} 的 Udio 验证邮件超时（>{max_wait}s）")


class UdioRegistrationFlow(BaseRegistrationFlow):
    """Udio 自动化注册流水线实现。"""

    platform_name = "udio"
    platform_table = "udio_accounts"

    def execute(self, context: FlowContext) -> RegistrationResult:
        driver = context.driver
        email = context.email
        start_ts = context.start_time

        # 1. 访问 Udio 首页
        logger.info("[1/5] 打开 Udio 官网...")
        driver.get("https://www.udio.com")
        time.sleep(5)

        # 清除 Cookie 弹窗并点击 Sign In
        driver.page.evaluate('''() => {
            const acceptBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').includes('ACCEPT ALL'));
            if (acceptBtn) acceptBtn.click();
            const banner = document.querySelector('#cookiescript_injected');
            if (banner) banner.remove();

            const signBtn = Array.from(document.querySelectorAll('button, a')).find(el => (el.innerText || '').trim().toLowerCase() === 'sign in');
            if (signBtn) signBtn.click();
        }''')
        time.sleep(2)

        # 2. 填写邮箱并请求 Magic Link
        logger.info("[2/5] 填充邮箱并触发 Magic Link...")
        driver.page.locator('input[type="email"]').fill(email)
        time.sleep(1)

        driver.page.locator('button:has-text("Continue with email")').click()
        time.sleep(3)

        # 3. 轮询收信并获取 Magic Link
        logger.info("[3/5] 轮询收件箱获取 Udio Magic Link...")
        magic_link = fetch_latest_udio_magic_link(email, after_ts=start_ts, max_wait=60)

        # 4. 访问确认链接并点击 Confirm
        logger.info("[4/5] 访问确认链接并完成会话确权...")
        driver.get(magic_link)
        time.sleep(4)

        driver.page.evaluate('''() => {
            const acceptBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').includes('ACCEPT ALL'));
            if (acceptBtn) acceptBtn.click();
            const banner = document.querySelector('#cookiescript_injected');
            if (banner) banner.remove();

            const confirmBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim().toLowerCase() === 'confirm');
            if (confirmBtn) confirmBtn.click();
        }''')
        time.sleep(5)

        # 5. 处理档案初始化 (Create Your Profile)
        logger.info("[5/5] 检测档案初始化界面 (Create Your Profile)...")
        username = _generate_random_username()
        driver.page.evaluate('''(uname) => {
            const banner = document.querySelector('#cookiescript_injected');
            if (banner) banner.remove();

            const userInput = document.querySelector('input#username') || 
                              Array.from(document.querySelectorAll('input')).find(i => (i.placeholder || '').toLowerCase().includes('user') || i.name === 'username');
            if (userInput && !userInput.value) {
                userInput.value = uname;
                userInput.dispatchEvent(new Event('input', { bubbles: true }));
                userInput.dispatchEvent(new Event('change', { bubbles: true }));
            }

            const createBtn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim().toLowerCase().includes('create profile'));
            if (createBtn) createBtn.click();
        }''', username)
        time.sleep(4)

        # 6. 提取 Supabase 认证凭据与 Cookies
        cookies_list = driver.page.context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in cookies_list}

        sb_chunks = []
        for i in range(5):
            k = f"sb-ssr-production-auth-token.{i}"
            if k in cookies_dict:
                sb_chunks.append(cookies_dict[k])
        full_sb_token = "".join(sb_chunks)

        access_token = full_sb_token
        refresh_token = ""

        ls_data = driver.page.evaluate('''() => {
            const ls = {};
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                ls[k] = localStorage.getItem(k);
            }
            return ls;
        }''')

        for k, v in ls_data.items():
            if "auth" in k.lower() or "supabase" in k.lower() or "token" in k.lower():
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, dict):
                        if "access_token" in parsed:
                            access_token = parsed["access_token"]
                            refresh_token = parsed.get("refresh_token", "")
                            break
                        if "currentSession" in parsed and isinstance(parsed["currentSession"], dict):
                            access_token = parsed["currentSession"].get("access_token", access_token)
                            refresh_token = parsed["currentSession"].get("refresh_token", "")
                            break
                except Exception:
                    pass

        return RegistrationResult(
            ok=True,
            email=email,
            platform=self.platform_name,
            access_token=access_token,
            refresh_token=refresh_token,
            cookies=cookies_dict,
            credits=10,
            status="active",
            metadata={"username": username},
        )

    def persist(self, context: FlowContext, result: RegistrationResult) -> bool:
        p = Path(context.db_path or DB_PATH)
        username = result.metadata.get("username", "")
        return save_udio_account_to_db(
            email=result.email,
            username=username,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            token_type="Bearer",
            credits=result.credits,
            cookies=result.cookies,
            proxy_used=context.proxy,
            payload=context.extra_payload,
            db_path=p,
        )

    def push(self, context: FlowContext, result: RegistrationResult) -> bool:
        p = Path(context.db_path or DB_PATH)
        pushed = push_account_to_music2api(
            email=result.email,
            access_token=result.access_token,
            refresh_token=result.refresh_token,
            cookies=result.cookies,
            credits=result.credits,
            db_path=p,
        )
        result.synced_to_gateway = pushed
        return pushed


# ============================================================
# 向后兼容公开 API (供 CLI、WebUI 与现有外部脚本调用)
# ============================================================

def pick_available_udio_account(
    db_path: Path | str | None = None,
    specific_email: Optional[str] = None,
) -> Dict[str, Any]:
    """从本地 SQLite 邮箱池中分配未在 udio_accounts 注册过的可用邮箱。"""
    p = Path(db_path or DB_PATH)
    init_udio_db(p)
    return pick_unregistered_email_for_platform(
        platform_table="udio_accounts",
        specific_email=specific_email,
        db_path=p,
    )


def register_single_udio_account(
    account_info: Dict[str, Any],
    proxy: str = "http://127.0.0.1:7890",
    headless: bool = True,
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """全自动完成单只 Udio 账号注册、Magic Link 确权与凭据提取。"""
    flow = UdioRegistrationFlow(db_path=db_path)
    res = flow.run(account_info=account_info, proxy=proxy, headless=headless)
    if res.ok:
        return {
            "ok": True,
            "email": res.email,
            "username": res.metadata.get("username", ""),
            "access_token": res.access_token[:30] + "..." if res.access_token else "",
            "credits": res.credits,
            "synced": res.synced_to_gateway,
        }
    return {
        "ok": False,
        "email": res.email,
        "error": res.error or "注册失败",
    }


def register_batch_udio_accounts(
    count: int = 1,
    specific_email: Optional[str] = None,
    proxy: str = "http://127.0.0.1:7890",
    headless: bool = True,
    db_path: Path | str | None = None,
) -> List[Dict[str, Any]]:
    """批量自动化注册 Udio 账号并推送到网关。"""
    p = Path(db_path or DB_PATH)
    init_udio_db(p)

    results = []
    success_count = 0

    for i in range(count):
        logger.info(f"\n--- [Udio 注册进度 {i+1}/{count}] ---")
        try:
            acc_info = pick_available_udio_account(p, specific_email=specific_email if i == 0 else None)
        except Exception as e:
            logger.error(f"[X] 分配邮箱失败: {e}")
            break

        res = register_single_udio_account(
            account_info=acc_info,
            proxy=proxy,
            headless=headless,
            db_path=p,
        )
        results.append(res)
        if res.get("ok"):
            success_count += 1

        if i < count - 1:
            time.sleep(8)

    logger.info(f"\n=== Udio 批量注册完成: 成功 {success_count}/{count} ===")
    return results
