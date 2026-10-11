# -*- coding: utf-8 -*-
"""Outlook Flow Trigger for PerimeterX / HUMAN Challenge

迁移自 references/captcha-solver/perimeterx/renderers/outlook.py
增强点：
1. 融合 core/human_dynamics.py 的生物拟人化键入 (防止在达挑战前被静默风控熔断)
2. 走通 Microsoft Live 抛弃型表单路径 (MemberName -> Password -> BirthDate -> Name)
3. 严格止步于 Name 步骤之后，不执行最终创建，仅用于触发 PerimeterX 并 Harvest 凭证
"""

from __future__ import annotations

import asyncio
import logging
import random
import string
from typing import Any, List

try:
    from core.human_dynamics import BiometricKeyboard
except ImportError:
    BiometricKeyboard = None

logger = logging.getLogger("perimeterx_renderer_outlook")

ENTRY_URL = (
    "https://go.microsoft.com/fwlink/p/?linkid=2125440"
    "&clcid=0x409&culture=en-us&country=us"
)

_FIRST_NAMES = [
    "Nathan", "Cherly", "Galuh", "Rendy", "Sinta", "Bagas", "Dinda", "Fajar",
    "Marcus", "Elena", "Lucas", "Sophia", "Oliver", "Emma", "Julian", "Clara"
]
_LAST_NAMES = [
    "Kusumo", "Halimah", "Pratama", "Wijaya", "Santoso", "Nugroho", "Saputra",
    "Miller", "Vance", "Kovacs", "Dubois", "Lindqvist", "Novak", "Schmidt"
]


async def _fill_field(page: Any, selectors: List[str], text: str) -> bool:
    for s in selectors:
        loc = page.locator(s).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click()
                await asyncio.sleep(random.uniform(0.1, 0.25))
                if BiometricKeyboard:
                    await BiometricKeyboard.type_playwright(page, text, typo_chance=0.01)
                else:
                    await loc.fill(text)
                return True
        except Exception:
            continue
    return False


async def _click_next(page: Any) -> bool:
    candidates = [
        '#iSignupAction',
        'button:has-text("Next")',
        'button:has-text("下一步")',
        'button[type="submit"]',
        '#nextButton',
    ]
    for s in candidates:
        loc = page.locator(s).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=3000)
                return True
        except Exception:
            continue
    return False


async def _pick_fluent_option(page: Any, trigger_selectors: List[str], option_index: int) -> bool:
    """Fluent UI 下拉框点击选择 (role=combobox -> role=option)"""
    for ts in trigger_selectors:
        loc = page.locator(ts).first
        try:
            if await loc.count() > 0 and await loc.is_visible():
                await loc.click(timeout=3000)
                await asyncio.sleep(0.4)
                opts = page.locator('[role="option"]')
                await opts.first.wait_for(state="visible", timeout=3000)
                count = await opts.count()
                if count > 0:
                    chosen = min(option_index, count - 1)
                    await opts.nth(chosen).click(timeout=2500)
                    await asyncio.sleep(0.3)
                    return True
        except Exception:
            continue
    return False


async def render_outlook_gate(page: Any) -> None:
    """推进 Outlook 注册表单直至 PerimeterX / HUMAN 挑战门渲染出现。
    仅录入一次性抛弃数据，在姓名确认后立即可见挑战门。
    """
    disposable_tag = (
        "".join(random.choices(string.ascii_lowercase, k=6))
        + str(random.randint(100, 999))
    )
    disposable_pwd = (
        "Px" + "".join(random.choices(string.ascii_letters + string.digits, k=11)) + "!8"
    )

    logger.info("🧭 正在导航至 Microsoft 注册入口: %s", ENTRY_URL)
    await page.goto(ENTRY_URL, wait_until="domcontentloaded", timeout=45000)
    await asyncio.sleep(5)  # 等待 OAuth 重定向稳定

    try:
        await page.wait_for_load_state("networkidle", timeout=8000)
    except Exception:
        pass

    # 1. 用户名
    logger.info("📝 正在录入触发用户名: %s", disposable_tag)
    await _fill_field(page, ['input[name="MemberName"]', 'input[type="email"]'], disposable_tag)
    await _click_next(page)
    await asyncio.sleep(2.5)

    # 2. 密码
    logger.info("🔑 正在录入触发密码")
    await _fill_field(page, ['input[name="Password"]', 'input[type="password"]'], disposable_pwd)
    await _click_next(page)
    await asyncio.sleep(2.5)

    # 3. 生日选项
    logger.info("🎂 正在选择出生日期")
    await _pick_fluent_option(
        page,
        ['#BirthMonthDropdown', '[aria-label="Birth month"]', '[aria-label="出生月份"]'],
        random.randint(0, 11),
    )
    await _pick_fluent_option(
        page,
        ['#BirthDayDropdown', '[aria-label="Birth day"]', '[aria-label="出生日期"]'],
        random.randint(0, 27),
    )
    await _fill_field(
        page,
        ['input[name="BirthYear"]', '[aria-label="Birth year"]', '#BirthYear', '[aria-label="出生年份"]'],
        str(random.randint(1989, 2002)),
    )
    await _click_next(page)
    await asyncio.sleep(2.5)

    # 4. 姓名步骤
    first_name = random.choice(_FIRST_NAMES)
    last_name = random.choice(_LAST_NAMES)
    logger.info("👤 正在录入姓名: %s %s", first_name, last_name)
    await _fill_field(page, ['#firstNameInput', 'input[name="FirstName"]'], first_name)
    await _fill_field(page, ['#lastNameInput', 'input[name="LastName"]'], last_name)
    await _click_next(page)

    logger.info("⏳ 姓名提交完成，等待 PerimeterX / HUMAN 挑战门渲染 (5~7s)...")
    await asyncio.sleep(6)
