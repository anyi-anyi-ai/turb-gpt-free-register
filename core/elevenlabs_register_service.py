# -*- coding: utf-8 -*-
"""
ElevenLabs (elevenlabs.io) 半自动化人机协作注册服务模块 (基于 BaseRegistrationFlow 模板方法重构)。
模式说明：
  1. 脚本全自动分配邮箱、生成高强密码、启动可视化指纹浏览器并填报表单
  2. 触发 Arkose Labs / hCaptcha 拼图验证码时，暂停并交由人工在弹窗中极速完成小动物旋转拼图
  3. 脚本实时监听拼图通关状态，自动无缝接管：
     - 调用 Microsoft Graph API 毫秒级轮询收取 ElevenLabs 激活邮件
     - 提取并自动访问验证链接完成账号激活确权
     - 自动提取登录会话凭据、Cookies 与 API Key
     - 本地落盘 turb.sqlite3 并实时推送到 music2api 网关
"""
from __future__ import annotations

import json
import logging
import os
import re
import secrets
import string
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.cloakbrowser_driver import build_cloak_driver
from core.flows.base import BaseRegistrationFlow
from core.flows.context import FlowContext, RegistrationResult
from core.flows.pool_helper import pick_unregistered_email_for_platform
from core.outlook_client import get_account_context, _http_session, _fetch_via
from core.elevenlabs2api_sync import (
    DEFAULT_DB_PATH,
    init_elevenlabs_db,
    save_elevenlabs_account_to_db,
    push_account_to_music2api,
)

logger = logging.getLogger("elevenlabs.register")
DB_PATH = DEFAULT_DB_PATH


def _generate_secure_password() -> str:
    """生成符合 ElevenLabs 强密码规范的高强度安全密码 (CSPRNG)。"""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    core = "".join(secrets.choice(chars) for _ in range(12))
    return f"Eleven{core}#2026!"


def fetch_latest_elevenlabs_verification_link(
    email: str,
    after_ts: float | None = None,
    max_wait: int = 70,
    poll_interval: int = 3,
) -> str:
    """通过 Graph API 轮询收件箱并抽取 ElevenLabs 邮箱激活链接。"""
    account = get_account_context(email)
    if account is None:
        raise RuntimeError(f"未找到 {email} 的账号上下文（请确认 refresh_token 是否存在）")

    deadline = time.time() + max_wait
    session = _http_session()

    logger.info(f"[Outlook] 开始轮询 {email} 的 ElevenLabs 激活邮件，最长等待 {max_wait}s...")

    while time.time() < deadline:
        emails = _fetch_via(session, "graph", account)
        for item in emails:
            body_txt = str(item.get("bodyText") or item.get("body") or item.get("content") or item.get("bodyPreview") or "")
            body_html = str(item.get("bodyHtml") or item.get("html") or item.get("body") or "")
            full_body = f"{body_txt}\n{body_html}"

            # 优先精确匹配含有 verifyEmail 与 oobCode 的 Firebase 激活链接
            matches = [l.replace("&amp;", "&") for l in re.findall(r'https://elevenlabs\.io[^\s"\'<>]+', full_body)]
            for link in matches:
                if "verifyEmail" in link or "oobCode" in link or "/action?" in link:
                    logger.info(f"[Outlook] 成功提取到精确激活链接: {link[:80]}...")
                    return link

            for link in matches:
                if any(k in link for k in ("verify", "confirm", "action")):
                    logger.info(f"[Outlook] 提取到备选激活链接: {link[:80]}...")
                    return link

        time.sleep(poll_interval)

    raise TimeoutError(f"等待 {email} 的 ElevenLabs 激活邮件超时（>{max_wait}s）")


class ElevenLabsRegistrationFlow(BaseRegistrationFlow):
    """ElevenLabs 注册流水线实现。"""

    platform_name = "elevenlabs"
    platform_table = "elevenlabs_accounts"

    def build_driver(self, context: FlowContext) -> Any:
        driver, _ = build_cloak_driver(
            proxy=context.proxy,
            headless=context.headless,
            account_email=context.email,
        )
        return driver

    def execute(self, context: FlowContext) -> RegistrationResult:
        driver = context.driver
        email = context.email
        password = context.password or context.extra_payload.get("password") or _generate_secure_password()
        context.password = password
        interactive_wait_timeout = context.metadata.get("interactive_wait_timeout", 150)
        p = Path(context.db_path or DB_PATH)

        logger.info(f"[*] 开始注册 ElevenLabs 账号: {email}")
        start_ts = time.time()

        try:
            # 1. 访问注册页并等待
            logger.info("[1/4] 打开 ElevenLabs 注册页面...")
            driver.get("https://elevenlabs.io/sign-up")
            time.sleep(5)

            # 2. 填写表单
            logger.info("[2/4] 自动填充注册邮箱与强密码...")
            driver.page.locator('input[type="email"]').first.fill(email)
            time.sleep(0.5)
            driver.page.locator('input[type="password"]').first.fill(password)
            time.sleep(1.0)

            # 3. 提交表单触发安全质询
            logger.info("[3/4] 提交注册表单，唤出质询弹窗...")
            driver.page.locator('input[type="password"]').first.press("Enter")
            time.sleep(3)

            # 3. 人机协作等待解决验证码
            try:
                from core.live_browser_hub import LiveBrowserHub
                LiveBrowserHub.ensure_server(8799)
                LiveBrowserHub.get_hub().attach_page(driver.page, auto_open=True)
            except Exception as _ex:
                logger.debug(f"LiveBrowser attach: {_ex}")

            print("\n" + "=" * 70, flush=True)
            print("  【人机零侵入 Web 协同】实时视窗已在系统默认浏览器自动打开！", flush=True)
            print(f"  账号：{email}", flush=True)
            print("  [*] 地址：http://127.0.0.1:8799/live (可在网页画布中直接点击旋转拼图)", flush=True)
            print("  [*] 拼图完成后，后台将自动检测并立即继续激活入库！", flush=True)
            print("=" * 70 + "\n", flush=True)

            captcha_solved = False
            verify_link = None
            wait_deadline = time.time() + interactive_wait_timeout

            session = _http_session()
            acc = get_account_context(email)

            while time.time() < wait_deadline:
                time.sleep(3)

                # 尝试直接从 Outlook 检查是否已经下发激活邮件
                if acc:
                    try:
                        emails = _fetch_via(session, "graph", acc)
                        for item in emails:
                            subj = str(item.get("subject") or "").lower()
                            sender = str(item.get("from") or "").lower()
                            full_body = f"{subj}\n{item.get('bodyText') or item.get('body') or item.get('content') or item.get('bodyPreview') or ''}\n{item.get('bodyHtml') or item.get('html') or ''}"
                            if any(k in subj for k in ("verify", "elevenlabs", "action required", "confirm your email")) or \
                               any(k in sender for k in ("elevenlabs", "team@elevenlabs")):
                                matches = re.findall(r'https://[^\s"\'<>]*elevenlabs\.io[^\s"\'<>]+(?:verify|action|confirm)[^\s"\'<>]*', full_body)
                                if not matches:
                                    matches = re.findall(r'https://[^\s"\'<>]*elevenlabs\.io[^\s"\'<>]+', full_body)
                                if matches:
                                    verify_link = matches[0].replace("&amp;", "&")
                                    logger.info(f"[√] Outlook 已收到激活邮件！验证码已通关: {verify_link}")
                                    captcha_solved = True
                                    break
                        if captcha_solved:
                            break
                    except Exception:
                        pass

                # 检查页面状态 (是否提示 Check your inbox / Verify your email)
                try:
                    state = driver.page.evaluate('''() => {
                        const text = (document.body && document.body.innerText) || '';
                        const hasVerify = text.includes('Verify your email') || text.includes('Check your inbox') || text.includes('Verification link sent') || text.includes('We sent an email');
                        const hasInputs = Boolean(document.querySelector('input[type="password"]'));
                        return { hasVerify, hasInputs };
                    }''')

                    if state.get("hasVerify"):
                        logger.info("[√] 页面已显示激活指引 (Verify your email)，验证码已通关！")
                        captcha_solved = True
                        break
                except Exception:
                    pass

            try:
                from core.live_browser_hub import LiveBrowserHub
                LiveBrowserHub.get_hub().detach_page()
            except Exception:
                pass

            if not captcha_solved:
                logger.warning("[!] 暂未检测到明确的通关标记，尝试继续轮询邮件...")

            # 4. 轮询收信并获取激活链接
            if not verify_link:
                logger.info("[4/4] 正在通过 Graph API 轮询收取 ElevenLabs 激活邮件...")
                verify_link = fetch_latest_elevenlabs_verification_link(email, after_ts=start_ts, max_wait=70)

            # 5. 访问激活链接确权
            logger.info(f"[*] 正在自动访问激活链接并完成账号确权: {verify_link}")
            driver.get(verify_link)
            time.sleep(6)

            # 5.1 点击 Email Verification 弹窗上的 Continue
            try:
                logger.info("[*] 检测并点击 Email Verification 确认弹窗的 Continue 按钮...")
                driver.page.evaluate('''() => {
                    const btns = Array.from(document.querySelectorAll('button, a'));
                    const cBtn = btns.find(b => (b.innerText || '').trim().toLowerCase() === 'continue');
                    if (cBtn) cBtn.click();
                }''')
                time.sleep(3)
            except Exception:
                pass

            # 5.2 如果页面回退到了 sign-in，自动输入密码完成二次登录确认
            if "/sign-in" in driver.page.url:
                logger.info("[*] 按照 ElevenLabs 要求完成注册后的二次密码确认登录...")
                try:
                    pwd_el = driver.page.locator('input[type="password"]').first
                    if pwd_el.count() > 0:
                        pwd_el.click()
                        pwd_el.fill(password)
                        time.sleep(0.5)
                        driver.page.evaluate('''() => {
                            const btn = Array.from(document.querySelectorAll('button')).find(b => (b.innerText || '').trim() === 'Sign in');
                            if (btn) btn.click();
                        }''')
                        time.sleep(8)
                except Exception as e:
                    logger.warning(f"[!] 二次登录提交异常: {e}")

            # 5.3 穿透 Onboarding 引导流程
            logger.info("[*] 正在穿透新用户引导流程 (Onboarding)...")
            for _ in range(8):
                time.sleep(2)
                url_now = driver.page.url
                if "/app" in url_now and "onboarding" not in url_now and "sign-in" not in url_now:
                    break
                try:
                    driver.page.evaluate('''() => {
                        const cb = document.querySelector('input[type="checkbox"], button[role="checkbox"]');
                        if (cb && !cb.checked) {
                            cb.click();
                        }
                        window.scrollTo(0, document.body.scrollHeight);
                        const btns = Array.from(document.querySelectorAll('button, a'));
                        const actionBtn = btns.find(b => {
                            const t = (b.innerText || '').trim().toLowerCase();
                            return t === 'continue' || t === 'next' || t === 'skip' || t === 'skip for now' || t === 'get started' || t === 'done';
                        });
                        if (actionBtn) actionBtn.click();
                    }''')
                except Exception:
                    pass

            # 5.4 提取 LocalStorage 中的 Firebase Auth JWT
            session_token = ""
            try:
                ls_data = driver.page.evaluate('''() => {
                    const ls = {};
                    for (let i = 0; i < localStorage.length; i++) {
                        const k = localStorage.key(i);
                        ls[k] = localStorage.getItem(k);
                    }
                    return ls;
                }''')
                for k, v in ls_data.items():
                    if "firebase:authUser" in k:
                        try:
                            u_obj = json.loads(v)
                            session_token = u_obj.get("stsTokenManager", {}).get("accessToken") or ""
                            if session_token:
                                logger.info(f"[√] 成功提取到官方 Firebase Bearer Token: {session_token[:20]}...")
                                break
                        except Exception:
                            pass
            except Exception:
                pass

            # 5.5 直达新版开发者路径提取或生成 xi-api-key
            api_key = ""
            try:
                logger.info("[*] 前往开发者 API Keys 页面提取或生成凭据 (/app/developers/api-keys)...")
                driver.get("https://elevenlabs.io/app/developers/api-keys")
                time.sleep(5)

                api_key = driver.page.evaluate('''() => {
                    const inputs = Array.from(document.querySelectorAll('input'));
                    const keyInput = inputs.find(i => (i.value || '').startsWith('xi-') || (i.value || '').length === 32);
                    if (keyInput) return keyInput.value;
                    const codes = Array.from(document.querySelectorAll('code, span, div')).map(c => (c.innerText || '').trim());
                    const found = codes.find(c => c.startsWith('xi-') || (c.length === 32 && /^[a-f0-9]+$/.test(c)));
                    return found || '';
                }''')

                if not api_key:
                    driver.page.evaluate('''() => {
                        const btns = Array.from(document.querySelectorAll('button'));
                        const cBtn = btns.find(b => {
                            const t = (b.innerText || '').toLowerCase();
                            return t.includes('create api key') || t.includes('create key') || t.includes('new key') || t.includes('add key');
                        });
                        if (cBtn) cBtn.click();
                    }''')
                    time.sleep(2)
                    driver.page.evaluate('''() => {
                        const inputs = Array.from(document.querySelectorAll('input[type="text"]'));
                        if (inputs.length > 0) {
                            inputs[0].value = 'music2api';
                            inputs[0].dispatchEvent(new Event('input', { bubbles: true }));
                        }
                        const modalBtns = Array.from(document.querySelectorAll('div[role="dialog"] button, button[type="submit"]'));
                        const confirmBtn = modalBtns.find(b => {
                            const t = (b.innerText || '').toLowerCase();
                            return t.includes('create') || t.includes('save') || t.includes('done') || t.includes('confirm');
                        });
                        if (confirmBtn) confirmBtn.click();
                    }''')
                    time.sleep(4)
                    api_key = driver.page.evaluate('''() => {
                        const inputs = Array.from(document.querySelectorAll('input'));
                        const keyInput = inputs.find(i => (i.value || '').startsWith('xi-') || (i.value || '').length === 32);
                        if (keyInput) return keyInput.value;
                        const codes = Array.from(document.querySelectorAll('code, span, div')).map(c => (c.innerText || '').trim());
                        const found = codes.find(c => c.startsWith('xi-') || (c.length === 32 && /^[a-f0-9]+$/.test(c)));
                        return found || '';
                    }''')

                if api_key:
                    logger.info(f"[√] 成功提取到官方 xi-api-key: {api_key[:8]}...")
            except Exception as e:
                logger.warning(f"[!] 提取 API Key 遇到非阻断性异常: {e}")

            # 提取 cookies
            cookies_list = driver.page.context.cookies()
            cookies_dict = {c["name"]: c["value"] for c in cookies_list}

            # 5.6 调用官方 API /v1/user/subscription 执行实测自检
            official_credits = 10000
            verified_live = False
            test_auth_headers = {}
            if api_key:
                test_auth_headers = {"xi-api-key": api_key}
            elif session_token:
                test_auth_headers = {"Authorization": f"Bearer {session_token}"}

            if test_auth_headers:
                try:
                    import httpx
                    logger.info("[*] 正在调用 ElevenLabs 官方接口 (/v1/user/subscription) 自检确权...")
                    with httpx.Client(proxy=context.proxy, timeout=15) as client:
                        resp = client.get("https://api.elevenlabs.io/v1/user/subscription", headers=test_auth_headers)
                        if resp.status_code == 200:
                            sub_data = resp.json()
                            official_credits = max(0, sub_data.get("character_limit", 10000) - sub_data.get("character_count", 0))
                            verified_live = True
                            logger.info(f"[√√√] 官方接口自检 100% 成功！实测可用额度: {official_credits:,} 字符")
                        else:
                            logger.warning(f"[!] 官方接口返回非 200 响应: HTTP {resp.status_code} {resp.text[:150]}")
                except Exception as e:
                    logger.warning(f"[!] 官方接口自检网络异常: {e}")

            return RegistrationResult(
                ok=True,
                email=email,
                platform=self.platform_name,
                access_token=session_token,
                api_key=api_key or (session_token[:20] + "..."),
                credits=official_credits,
                cookies=cookies_dict,
                status="active",
                metadata={
                    "password": password,
                    "api_key": api_key,
                    "session_token": session_token,
                    "verified_live": verified_live,
                },
            )

        except Exception as exc:
            try:
                save_elevenlabs_account_to_db(
                    email=email,
                    password=password,
                    status="failed",
                    credits=0,
                    payload={"error": str(exc)},
                    db_path=p,
                )
            except Exception:
                pass
            raise

    def persist(self, context: FlowContext, result: RegistrationResult) -> bool:
        password = result.metadata.get("password") or context.password or ""
        api_key = result.metadata.get("api_key") or result.api_key or ""
        session_token = result.metadata.get("session_token") or result.access_token or ""
        p = Path(context.db_path or DB_PATH)
        return save_elevenlabs_account_to_db(
            email=result.email,
            password=password,
            api_key=api_key,
            session_token=session_token,
            credits=result.credits,
            cookies=result.cookies,
            proxy_used=context.proxy or "",
            status="active" if result.ok else "failed",
            payload=context.extra_payload,
            db_path=p,
        )

    def push(self, context: FlowContext, result: RegistrationResult) -> bool:
        api_key = result.metadata.get("api_key") or result.api_key or ""
        session_token = result.metadata.get("session_token") or result.access_token or ""
        p = Path(context.db_path or DB_PATH)
        pushed = push_account_to_music2api(
            email=result.email,
            api_key=api_key,
            session_token=session_token,
            cookies=result.cookies,
            credits=result.credits,
            db_path=p,
        )
        result.synced_to_gateway = pushed
        return pushed

    def cleanup(self, context: FlowContext) -> None:
        if context.driver:
            try:
                if hasattr(context.driver, "page") and context.driver.page:
                    try:
                        context.driver.page.close()
                    except Exception:
                        pass
                if hasattr(context.driver, "context") and context.driver.context:
                    try:
                        context.driver.context.close()
                    except Exception:
                        pass
                context.driver.quit()
            except Exception:
                pass
            context.driver = None


# ============================================================
# 向后兼容公开 API (供 CLI、WebUI 与现有外部脚本调用)
# ============================================================

def pick_available_elevenlabs_account(
    db_path: Path | str | None = None,
    specific_email: Optional[str] = None,
) -> Dict[str, Any]:
    """从本地 SQLite 邮箱池中分配未在 elevenlabs_accounts 注册过的可用邮箱。"""
    p = Path(db_path or DB_PATH)
    init_elevenlabs_db(p)
    return pick_unregistered_email_for_platform(
        platform_table="elevenlabs_accounts",
        specific_email=specific_email,
        db_path=p,
    )


def register_single_elevenlabs_account(
    account_info: Dict[str, Any],
    proxy: str = "",
    headless: bool = False,
    db_path: Path | str | None = None,
    interactive_wait_timeout: int = 150,
) -> Dict[str, Any]:
    if proxy and proxy.lower() in ("direct", "none", "local"):
        proxy = ""
    """
    人机协作单号注册（直接使用注册机原生 CloakBrowser 指纹浏览器有头模式）：
      - 驱动：注册机原生 CloakBrowser 指纹对抗与出口代理
      - 交互：有头弹出窗口，自动填报并回车提交，唤出质询弹窗
      - 协作：人工在弹出的指纹浏览器中点击完成镜像翻转拼图
      - 后置：自动 Graph API 轮询收信、激活确权、提取 API Key 并同步至网关
    """
    p = Path(db_path or DB_PATH)
    init_elevenlabs_db(p)

    flow = ElevenLabsRegistrationFlow(db_path=p)
    res = flow.run(
        account_info=account_info,
        proxy=proxy,
        headless=headless,
        interactive_wait_timeout=interactive_wait_timeout,
    )

    if res.ok:
        return {
            "ok": True,
            "email": res.email,
            "password": res.metadata.get("password", ""),
            "api_key": res.api_key or (res.access_token[:20] + "..."),
            "credits": res.credits,
            "synced": res.synced_to_gateway,
            "verified_live": res.metadata.get("verified_live", False),
        }
    else:
        return {
            "ok": False,
            "email": res.email,
            "error": res.error,
        }


def wait_for_user_or_timeout(prompt: str, timeout: int = 4) -> bool:
    """
    等待用户按键确认或倒计时超时：
    - 返回 True: 继续注册下一个账号（按了 Enter / Space 或倒计时自然结束）
    - 返回 False: 退出后续注册（按了 Q / q / ESC）
    """
    print(prompt, flush=True)

    if sys.platform == "win32":
        try:
            import msvcrt

            remaining = timeout
            while remaining > 0:
                sys.stdout.write(f"\r[*] 倒计时 [{remaining:2d}s] - 按 [Enter] 立即注册下一个，按 [Q] 退出流水线... ")
                sys.stdout.flush()

                sub_deadline = time.time() + 1.0
                while time.time() < sub_deadline:
                    if msvcrt.kbhit():
                        ch = msvcrt.getwch()
                        if ch in ('q', 'Q', '\x1b'):
                            print(f"\n[*] 接收到终止指令 ('{ch}')，已停止后续注册。")
                            return False
                        elif ch in ('\r', '\n', ' '):
                            print("\n[*] 用户确认，立即启动下一个账号注册...")
                            return True
                    time.sleep(0.05)
                remaining -= 1

            print("\n[*] 倒计时完成，自动进入下一个账号注册流程...")
            return True
        except Exception:
            pass

    # 非 Windows 或 msvcrt 异常时的回退兼容
    try:
        time.sleep(timeout)
        return True
    except KeyboardInterrupt:
        print("\n[*] 接收到中断信号，退出注册。")
        return False


def print_account_summary_card(
    result: Dict[str, Any],
    current_index: int,
    total_target: int | str,
) -> None:
    """打印账号确权成功确认卡片。"""
    email = result.get("email") or ""
    key = result.get("api_key") or "SessionOnly"
    credits = result.get("credits", 10000)
    synced = result.get("synced", False)

    print("\n" + "=" * 76)
    print(f"  [√] 【ElevenLabs 账号确权成功】 进度: [{current_index}/{total_target}]")
    print(f"  * 账号邮箱:     {email}")
    print(f"  * 初始额度:     {credits:,} 字符 (Credits)")
    print(f"  * API 凭据:     {key}")
    print(f"  * 账号激活:     已通过 (Email Verified)")
    print(f"  * 本地持久化:   turb.sqlite3 (elevenlabs_accounts)")
    print(f"  * 聚合网关同步: {'已成功挂载 (music2api :18620)' if synced else '待补推'}")
    print("=" * 76 + "\n", flush=True)


def register_batch_elevenlabs_accounts(
    count: int = 5,
    continuous: bool = False,
    specific_email: Optional[str] = None,
    proxy: str = "",
    headless: bool = False,
    db_path: Path | str | None = None,
) -> List[Dict[str, Any]]:
    """
    批量/连续人机协作注册 ElevenLabs 账号：
    每个账号注册完成并确认入库后，自动关闭当前窗口，倒计时后平滑过渡至下一个账号。
    """
    if proxy and proxy.lower() in ("direct", "none", "local"):
        proxy = ""
    p = Path(db_path or DB_PATH)
    init_elevenlabs_db(p)

    results: List[Dict[str, Any]] = []
    success_count = 0
    target_str = "连续流转" if continuous else f"{count}"

    idx = 0
    while True:
        idx += 1
        if not continuous and idx > count:
            break

        logger.info("\n==================================================")
        logger.info(f"  [ElevenLabs 人机协作] 正在准备第 {idx} 个账号 (计划: {target_str})")
        logger.info("==================================================")

        try:
            acc_info = pick_available_elevenlabs_account(p, specific_email=specific_email if idx == 1 else None)
        except Exception as e:
            logger.error(f"[X] 分配新邮箱失败: {e}")
            break

        res = register_single_elevenlabs_account(
            account_info=acc_info,
            proxy=proxy,
            headless=headless,
            db_path=p,
        )
        results.append(res)

        if res.get("ok"):
            success_count += 1
            print_account_summary_card(res, idx, target_str)
        else:
            print("\n" + "!" * 76)
            print(f"  [X] 账号 [{res.get('email')}] 注册未成功: {res.get('error')}")
            print("!" * 76 + "\n", flush=True)

        # 检查是否还有下一个账号
        has_more = continuous or (idx < count)
        if has_more:
            prompt_msg = f"[*] 当前账号 [{res.get('email')}] 已处理完成。即将继续注册下一个账号..."
            should_continue = wait_for_user_or_timeout(prompt_msg, timeout=4)
            if not should_continue:
                logger.info("[*] 用户主动中止，停止后续账号注册。")
                break

    total_attempted = len(results)
    logger.info(f"\n=== ElevenLabs 注册流转完毕: 成功 {success_count}/{total_attempted} (目标: {target_str}) ===")
    return results
