# -*- coding: utf-8 -*-
"""
Muse (muse.ai) 自动化注册服务模块 (基于 BaseRegistrationFlow 模板方法重构)
驱动层：基于 CloakBrowser 指纹浏览器 + 美国纯净住宅代理出口
邮箱层：深度打通 UNED 学生邮箱 (@alumno.uned.es) 与 Outlook 自动转发 Graph API 毫秒级接码
业务层：完成 Meta/Muse 注册流程，提取核心会话 Cookie (hatch_sess, hatch_gw, hatch_vml 等)，
        自动存入 SQLite 库并通过 muse2api_sync 推送至独立 muse2api 网关。
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.cloakbrowser_driver import build_cloak_driver
from core.flows.base import BaseRegistrationFlow
from core.flows.context import FlowContext, RegistrationResult
from core.outlook_client import get_account_context, fetch_latest_otp, _CONTEXT_CACHE
from core.muse2api_sync import init_muse_db, save_muse_account_to_db, push_account_to_muse2api, check_muse2api_health
from core.profile_utils import generate_random_birthday
from core.name_samples import random_display_name

logger = logging.getLogger("muse.register")

_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = _ROOT / "turb.sqlite3"

MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December"
]


def _is_account_healthy(acc_ctx) -> bool:
    try:
        from core.outlook_client import _fetch_via, _http_session
        s = _http_session()
        emails = _fetch_via(s, "graph", acc_ctx)
        subjects = [str(e.get("subject") or "").lower() for e in emails[:3]]
        return not any("bloqueada" in sub for sub in subjects)
    except Exception:
        return False


def pick_available_student_account(specific_email: Optional[str] = None, db_path: Path | str | None = None) -> Dict[str, Any]:
    """
    从 SQLite 邮箱池或 uned_accounts 中提取尚未注册 Muse 的有效学生邮箱。
    自动检测并跳过已被学校风控锁定的邮箱。
    """
    p = Path(db_path or DB_PATH)
    init_muse_db(p)

    with sqlite3.connect(p) as conn:
        if specific_email:
            ident = specific_email.split("@")[0].strip().lower()
            u_row = conn.execute("""
                SELECT email, uned_identifier, full_name, birth_date, payload_json
                FROM uned_accounts
                WHERE LOWER(uned_identifier) = ? OR LOWER(email) = ?
                LIMIT 1;
            """, (ident, specific_email.lower())).fetchone()
            if u_row:
                fwd_email = u_row[0]
                uid = u_row[1]
                s_email = f"{uid}@alumno.uned.es"
                payload = json.loads(u_row[4]) if u_row[4] else {}
                return {
                    "student_email": s_email,
                    "forward_outlook": fwd_email,
                    "full_name": u_row[2] or random_display_name(),
                    "birth_date": u_row[3] or generate_random_birthday(),
                    "uned_identifier": uid,
                    "source": "uned",
                }

        # 1. 优先查 email_pool 中 source='uned' 且未在 muse_accounts 注册过的账号
        try:
            rows = conn.execute("""
                SELECT ep.email, ep.payload
                FROM email_pool ep
                WHERE ep.source = 'uned'
                  AND ep.email NOT IN (SELECT email FROM muse_accounts)
                ORDER BY ep.id ASC;
            """).fetchall()

            for row in rows:
                email = row[0]
                if "lli59" in email.lower():
                    continue
                payload = json.loads(row[1]) if row[1] else {}
                fwd = payload.get("forward_outlook", "")
                if fwd:
                    acc_ctx = get_account_context(fwd)
                    if acc_ctx and _is_account_healthy(acc_ctx):
                        return {
                            "student_email": email,
                            "forward_outlook": fwd,
                            "full_name": payload.get("full_name") or random_display_name(),
                            "birth_date": payload.get("birth_date") or generate_random_birthday(),
                            "uned_identifier": payload.get("uned_identifier") or email.split("@")[0],
                            "source": "uned",
                        }
        except sqlite3.OperationalError:
            pass

        # 2. 兜底查 uned_accounts 表
        u_rows = conn.execute("""
            SELECT ua.email, ua.uned_identifier, ua.full_name, ua.birth_date, ua.payload_json
            FROM uned_accounts ua
            WHERE ua.status = 'activated'
              AND (ua.uned_identifier || '@alumno.uned.es') NOT IN (SELECT email FROM muse_accounts)
            ORDER BY ua.id ASC;
        """).fetchall()

        for u_row in u_rows:
            fwd_email = u_row[0]
            uid = u_row[1]
            s_email = f"{uid}@alumno.uned.es"
            acc_ctx = get_account_context(fwd_email)
            if acc_ctx and _is_account_healthy(acc_ctx):
                payload = json.loads(u_row[4]) if u_row[4] else {}
                return {
                    "student_email": s_email,
                    "forward_outlook": fwd_email,
                    "full_name": u_row[2] or random_display_name(),
                    "birth_date": u_row[3] or generate_random_birthday(),
                    "uned_identifier": uid,
                    "source": "uned",
                }

        # 3. 兜底查 uned_accounts 表
        u_row = conn.execute("""
            SELECT ua.email, ua.uned_identifier, ua.full_name, ua.birth_date, ua.payload_json
            FROM uned_accounts ua
            WHERE ua.status = 'activated'
              AND (ua.uned_identifier || '@alumno.uned.es') NOT IN (SELECT email FROM muse_accounts)
            LIMIT 1;
        """).fetchone()

        if u_row:
            fwd_email = u_row[0]
            uid = u_row[1]
            s_email = f"{uid}@alumno.uned.es"
            payload = json.loads(u_row[4]) if u_row[4] else {}
            return {
                "student_email": s_email,
                "forward_outlook": fwd_email,
                "full_name": u_row[2] or random_display_name(),
                "birth_date": u_row[3] or generate_random_birthday(),
                "uned_identifier": uid,
                "source": "uned",
            }

    raise RuntimeError("当前数据库中无可用的 UNED 学生邮箱或所有学生邮箱已注册 Muse！")


def _parse_birthday(birth_date_str: str) -> tuple[int, int, int]:
    if not birth_date_str:
        return (15, 6, 1998)
    parts = re.split(r"[-/.]", str(birth_date_str).strip())
    if len(parts) == 3:
        if len(parts[0]) == 4:
            return (int(parts[2]), int(parts[1]), int(parts[0]))
        else:
            return (int(parts[0]), int(parts[1]), int(parts[2]))
    return (15, 6, 1998)


def _handle_post_signup_flow(page, full_name: str, birth_date: str, invite_code: Optional[str] = None) -> None:
    """处理注册成功后的补充姓名、生日、服务条款等交互界面 (Finish creating your account)。"""
    try:
        # 1. 轮询检测资料完善表单是否出现
        for _ in range(8):
            try:
                present = page.evaluate("""() => {
                    const fname = document.querySelector("input[aria-label='First name']") || document.querySelector("input[placeholder='First name']");
                    return !!fname;
                }""")
                if present:
                    break
            except Exception:
                pass
            time.sleep(1.0)

        # 2. 解析姓名与合规生日
        day, month, year = _parse_birthday(birth_date)
        if year > 2005:
            year = 1998
        names = full_name.strip().split()
        first_name = names[0] if names else "Alex"
        last_name = " ".join(names[1:]) if len(names) > 1 else "Taylor"

        filled = False
        try:
            filled = page.evaluate("""(data) => {
                function setInputValue(input, val) {
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, "value").set;
                    setter.call(input, val);
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                    input.dispatchEvent(new Event('change', { bubbles: true }));
                }

                function setSelectValue(select, val) {
                    const setter = Object.getOwnPropertyDescriptor(window.HTMLSelectElement.prototype, "value").set;
                    setter.call(select, val);
                    select.dispatchEvent(new Event('change', { bubbles: true }));
                    select.dispatchEvent(new Event('input', { bubbles: true }));
                }

                const fname = document.querySelector("input[aria-label='First name']") || document.querySelector("input[placeholder='First name']");
                const lname = document.querySelector("input[aria-label='Last name']") || document.querySelector("input[placeholder='Last name']");
                if (fname) setInputValue(fname, data.first_name);
                if (lname) setInputValue(lname, data.last_name);

                const selects = Array.from(document.querySelectorAll("select"));
                if (selects.length >= 3) {
                    setSelectValue(selects[0], String(data.month));
                    setSelectValue(selects[1], String(data.day));
                    setSelectValue(selects[2], String(data.year));
                }

                return !!fname;
            }""", {"first_name": first_name, "last_name": last_name, "month": month, "day": day, "year": year})
        except Exception:
            pass

        if filled:
            logger.info(f"[MuseReg] 发现完善资料表单，已填充: {first_name} {last_name} | 生日: {year}-{month:02d}-{day:02d}")
            time.sleep(1.0)
            logger.info("[MuseReg] 点击资料页 Confirm 按钮完成创建 Meta 账号...")
            try:
                page.evaluate("""() => {
                    const btn = document.querySelector("button[type='submit']");
                    if (btn && !btn.disabled) btn.click();
                }""")
            except Exception:
                pass
            time.sleep(8.0)

        # 3. 检查是否有邀请码兑换入口
        if invite_code:
            try:
                invite_btn = page.locator("button:has-text('invite code'), a:has-text('invite code'), button:has-text('Redeem')")
                if invite_btn.count() > 0 and invite_btn.first.is_visible():
                    logger.info(f"[MuseReg] 发现邀请码入口: {invite_btn.first.inner_text()}，准备自动兑现...")
                    invite_btn.first.click()
                    time.sleep(1.5)
                code_inp = page.locator("input[placeholder*='code' i], input[aria-label*='code' i]")
                if code_inp.count() > 0 and code_inp.first.is_visible():
                    cur_val = code_inp.first.input_value()
                    if not cur_val:
                        logger.info(f"[MuseReg] 自动填入专属邀请码: {invite_code}")
                        code_inp.first.fill(invite_code)
                        time.sleep(0.8)
                        submit_btn = page.locator("button[type='submit'], button:has-text('Redeem'), button:has-text('Submit'), button:has-text('Continue')")
                        if submit_btn.count() > 0 and submit_btn.first.is_visible():
                            submit_btn.first.click()
                            time.sleep(3.0)
            except Exception as exc:
                logger.debug(f"[MuseReg] 自动输入邀请码步骤已跳过: {exc}")

        # 4. 检查是否展示 Join waitlist
        for _ in range(4):
            try:
                waitlist_btn = page.locator("button:has-text('Join waitlist')")
                if waitlist_btn.count() > 0 and waitlist_btn.first.is_visible():
                    logger.info("[MuseReg] 检测到 'Join waitlist' 排队按钮，点击加入等待名单...")
                    waitlist_btn.first.click()
                    time.sleep(3.0)
                    break
            except Exception:
                pass
            time.sleep(1.0)

        # 5. 处理可能存在的引导向导
        for _ in range(3):
            try:
                next_btn = page.locator("button:has-text('Next'), button:has-text('Continue'), button:has-text('Get started'), button:has-text('Done'), button:has-text('Agree')")
                if next_btn.count() > 0 and next_btn.first.is_visible():
                    logger.info(f"[MuseReg] 发现引导按钮，点击继续: {next_btn.first.inner_text()}")
                    next_btn.first.click()
                    time.sleep(2.0)
            except Exception:
                pass

    except Exception as e:
        logger.debug(f"[MuseReg] 完善资料交互处理异常: {e}")


class MuseRegistrationFlow(BaseRegistrationFlow):
    """Muse (muse.ai) 自动化注册流水线实现。"""

    platform_name = "muse"
    platform_table = "muse_accounts"

    def pick_account(self, specific_email: Optional[str] = None) -> Dict[str, Any]:
        info = pick_available_student_account(specific_email=specific_email, db_path=self.db_path)
        return {
            "email": info["student_email"],
            "payload": info,
            "source": info.get("source", "uned"),
        }

    def build_driver(self, context: FlowContext) -> Any:
        country = context.metadata.get("country", "US")
        driver, _ = build_cloak_driver(
            proxy=context.proxy,
            country=country,
            headless=context.headless,
            account_email=context.email,
        )
        return driver

    def execute(self, context: FlowContext) -> RegistrationResult:
        driver = context.driver
        email = context.email
        student_info = context.extra_payload or {}
        forward_email = student_info.get("forward_outlook", "")
        full_name = student_info.get("full_name") or random_display_name()
        birth_date = student_info.get("birth_date") or generate_random_birthday()
        invite_code = context.metadata.get("invite_code", "0EZCBM")
        otp_max_wait = context.metadata.get("otp_max_wait", 75)

        # 绑定 Outlook 接码上下文
        if forward_email:
            fwd_ctx = get_account_context(forward_email)
            if fwd_ctx:
                _CONTEXT_CACHE[email.lower()] = fwd_ctx
                logger.info(f"🔗 [Context] 已绑定 {email} -> {forward_email} Microsoft Graph 上下文")

        start_ts = time.time() - 3.0

        # [步骤 1] 打开 Muse 入口
        start_url = f"https://muse.ai/invite/{invite_code}" if invite_code else "https://muse.ai/"
        logger.info(f"[MuseReg] 导航至 {start_url} ...")
        driver.get(start_url)
        time.sleep(3.5)

        input_selector = "input[placeholder*='email' i], input[type='email'], input[type='text']"
        try:
            driver.page.wait_for_selector(input_selector, timeout=15000)
        except Exception:
            login_btn = "button:has-text('Log in'), a:has-text('Log in')"
            if driver.page.locator(login_btn).count() > 0:
                driver.page.click(login_btn)
                time.sleep(2)
            driver.page.wait_for_selector(input_selector, timeout=12000)

        # [步骤 2] 填入学生邮箱并点击 Continue
        logger.info(f"[MuseReg] 填入学生邮箱: {email}")
        driver.page.click(input_selector)
        time.sleep(0.3)
        driver.page.fill(input_selector, email)
        time.sleep(0.8)

        continue_btn = "button:has-text('Continue'), button[type='submit']"
        driver.page.wait_for_selector(continue_btn, timeout=10000)
        driver.page.click(continue_btn)
        logger.info("[MuseReg] 已点击 Continue，等待进入验证码核验界面...")

        # [步骤 3] 等待 OTP 界面并提取
        otp_selector = "input[autocomplete='one-time-code'], input[maxlength='6'], input[type='text'].sr-only"
        driver.page.wait_for_selector(otp_selector, timeout=15000)
        logger.info("[MuseReg] 验证码输入框已就绪，正在通过 Graph API 轮询收件箱...")

        otp_code = fetch_latest_otp(email, after_ts=start_ts, max_wait=otp_max_wait)
        logger.info(f"🎉 [MuseReg] 成功获取到 Muse 6位 OTP 验证码: {otp_code}")

        # [步骤 4] 填入验证码并确认
        logger.info(f"[MuseReg] 正在填入验证码: {otp_code}")
        driver.page.evaluate("""() => {
            const inp = document.querySelector("input[autocomplete='one-time-code']") || document.querySelector("input[maxlength='6']");
            if (inp) {
                inp.focus();
                inp.removeAttribute('readonly');
                inp.removeAttribute('disabled');
            }
        }""")
        time.sleep(0.5)
        driver.page.keyboard.type(otp_code, delay=80)
        time.sleep(1.2)

        logger.info("[MuseReg] 正在提交验证码 (点击 Confirm)...")
        driver.page.evaluate("""() => {
            const btn = Array.from(document.querySelectorAll("button")).find(b => (b.innerText || '').includes('Confirm'));
            if (btn) {
                btn.removeAttribute('disabled');
                btn.click();
            }
        }""")
        time.sleep(1.0)
        try:
            driver.page.locator("button:has-text('Confirm')").click(timeout=3000)
        except Exception:
            pass

        logger.info("[MuseReg] 已提交验证码，等待登录/后续引导跳转 (10s)...")
        time.sleep(10.0)

        # [步骤 5] 处理新账号引导信息
        _handle_post_signup_flow(driver.page, full_name, birth_date, invite_code=invite_code)

        # [步骤 6] 捕获当前会话所有 Cookie
        raw_cookies = driver.page.context.cookies()
        cookies_dict = {c["name"]: c["value"] for c in raw_cookies if c.get("name") and c.get("value")}
        exp_dict = {c["name"]: int(c["expires"]) for c in raw_cookies if c.get("name") and c.get("expires") and int(c.get("expires", 0)) > 0}

        essential = {k: cookies_dict[k] for k in ("hatch_sess", "hatch_gw", "hatch_vml", "hatch_native_auth_device") if k in cookies_dict}
        logger.info(f"🍪 [MuseReg] 成功截获 {len(cookies_dict)} 个 Cookie，核心项包含: {list(essential.keys())}")

        if "hatch_sess" not in cookies_dict and "hatch_vml" not in cookies_dict:
            raise RuntimeError(f"未能截获到核心 hatch_* 会话 Cookie: {list(cookies_dict.keys())}")

        return RegistrationResult(
            ok=True,
            email=email,
            platform=self.platform_name,
            access_token=cookies_dict.get("hatch_sess", ""),
            cookies=cookies_dict,
            status="active",
            metadata={
                "essential_cookies": essential,
                "expires_dict": exp_dict,
                "student_info": student_info,
                "full_name": full_name,
                "birth_date": birth_date,
            },
        )

    def persist(self, context: FlowContext, result: RegistrationResult) -> bool:
        full_name = result.metadata.get("full_name", "")
        birth_date = result.metadata.get("birth_date", "")
        student_info = result.metadata.get("student_info") or context.extra_payload or {}
        p = Path(context.db_path or DB_PATH)
        return save_muse_account_to_db(
            email=result.email,
            cookies=result.cookies,
            full_name=full_name,
            birth_date=birth_date,
            proxy_used=context.proxy or "",
            extra_payload={"student_info": student_info, "registered_at": datetime.now().isoformat()},
            db_path=p,
        )

    def push(self, context: FlowContext, result: RegistrationResult) -> bool:
        exp_dict = result.metadata.get("expires_dict", {})
        p = Path(context.db_path or DB_PATH)
        synced = push_account_to_muse2api(
            email=result.email,
            cookies=result.cookies,
            expires=exp_dict,
            label=result.email,
            db_path=p,
        )
        result.synced_to_gateway = synced
        return synced


# ============================================================
# 向后兼容公开 API (供 CLI、WebUI 与现有外部脚本调用)
# ============================================================

def run_muse_registration(
    student_info: Optional[Dict[str, Any]] = None,
    proxy: Optional[str] = None,
    country: str = "US",
    headless: bool = True,
    push_to_gateway: bool = True,
    otp_max_wait: int = 75,
    invite_code: Optional[str] = "0EZCBM",
    db_path: Path | str | None = None,
) -> Dict[str, Any]:
    """执行单个 Muse 账号全自动注册流水线。"""
    p = Path(db_path or DB_PATH)
    init_muse_db(p)

    if not student_info:
        student_info = pick_available_student_account(db_path=p)

    student_email = student_info["student_email"]
    account_info = {
        "email": student_email,
        "payload": student_info,
        "source": student_info.get("source", "uned"),
    }

    if not proxy:
        proxy = os.getenv("MUSE_PROXY", "http://US:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260")

    flow = MuseRegistrationFlow(db_path=p)
    res = flow.run(
        account_info=account_info,
        proxy=proxy,
        headless=headless,
        auto_push=push_to_gateway,
        country=country,
        otp_max_wait=otp_max_wait,
        invite_code=invite_code,
    )

    return {
        "ok": res.ok,
        "email": res.email,
        "proxy": proxy,
        "cookies": res.cookies,
        "essential_cookies": res.metadata.get("essential_cookies", {}),
        "synced": res.synced_to_gateway,
        "error": res.error,
    }
