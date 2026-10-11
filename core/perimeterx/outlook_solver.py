# -*- coding: utf-8 -*-
"""PerimeterX / HUMAN 'Press & Hold' Solver Core

核心逻辑与机理：
1. PerimeterX 的 "Press & Hold" 表面上是长按按钮，本质是在后台 Web Worker 执行 SHA-256 hashcash PoW，
   并在 Sensor-VM 中对光标轨迹、按压时延、生理微颤 (Micro-tremor) 进行生物力学打分。
2. 真实长按 + 拟人生理颤动使 Sensor-VM 打高分，服务端最终下发认证密匙 `_px3`。
3. `_px3` 与 Client IP、User-Agent、`_pxvid` 绑定。
4. 本模块支持：
   - 现场即时解密 (Inline solve): 直接解决当前已渲染的注册/登录挑战
   - 独立凭证收割 (Harvest-only solve): 抛弃型流程驱动，预先收割 `_px3` Cookie 捆绑包供重放
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import logging
import random
import time
from typing import Any, Dict, List, Optional

from common.stealth_playwright import async_playwright
from core.human_dynamics import BiometricMouse
from .detector import detect_perimeterx_gate, is_perimeterx_visible

logger = logging.getLogger("perimeterx_solver")

PX_COOKIE_NAMES = ("_px3", "_pxvid", "_pxde", "pxcts")


@dataclass
class PerimeterXSolution:
    solved: bool = False
    px3: Optional[str] = None
    cookies: Dict[str, str] = field(default_factory=dict)
    cookie_header: Optional[str] = None
    user_agent: Optional[str] = None
    gate_reached: bool = False
    press_hold_actuated: bool = False
    px3_rotated: bool = False
    attempts: int = 0
    elapsed: float = 0.0
    error: Optional[str] = None
    replay_contract: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "solved": self.solved,
            "px3": self.px3,
            "cookies": self.cookies,
            "cookie_header": self.cookie_header,
            "user_agent": self.user_agent,
            "gate_reached": self.gate_reached,
            "press_hold_actuated": self.press_hold_actuated,
            "px3_rotated": self.px3_rotated,
            "attempts": self.attempts,
            "elapsed": self.elapsed,
            "error": self.error,
            "replay_contract": self.replay_contract,
        }


async def _extract_px_cookies(context: Any) -> Dict[str, str]:
    try:
        cookies = await context.cookies()
        return {
            c["name"]: c["value"]
            for c in cookies
            if c.get("name") in PX_COOKIE_NAMES
        }
    except Exception:
        return {}


async def _biometric_press_hold(page: Any, target: Dict[str, Any],
                                hold_min: float = 4.5, hold_max: float = 9.0) -> bool:
    """在挑战目标区域执行带有微颤与生理漂移的真实长按 (Actuate Biometric Press-and-Hold)"""
    bx = target["x"]
    by = target["y"]
    bw = target["width"]
    bh = target["height"]

    # 贝塔随机偏置，避免每次绝对居中
    cx = bx + bw * random.betavariate(2.8, 2.8)
    cy = by + bh * random.betavariate(2.8, 2.8)

    hold_duration = random.uniform(hold_min, hold_max)
    logger.info("🖱️ [PerimeterX] 瞄准长按区域 (%.1f, %.1f)，计划按压时长: %.2fs", cx, cy, hold_duration)

    try:
        # 1. 拟人平滑移动至目标点
        await BiometricMouse.move_playwright(page, cx, cy)
        await asyncio.sleep(random.uniform(0.12, 0.28))

        # 2. 模拟真实下压
        await page.mouse.down()
        start_t = time.monotonic()

        # 3. 按压保持阶段的肌肉微颤动 (±1.2px Tremor)，满足 Sensor-VM 生物判定
        while time.monotonic() - start_t < hold_duration:
            tremor_x = cx + random.uniform(-1.2, 1.2)
            tremor_y = cy + random.uniform(-1.2, 1.2)
            await page.mouse.move(tremor_x, tremor_y)
            await asyncio.sleep(0.4)

            # 提前检测是否已验证通过门已关闭
            if not await is_perimeterx_visible(page):
                logger.info("🎯 [PerimeterX] 检测到挑战门在按压过程中已提前通过消失！")
                break

        # 4. 释放鼠标
        await page.mouse.up()
        await asyncio.sleep(random.uniform(0.3, 0.6))
        return True
    except Exception as exc:
        logger.warning("❌ [PerimeterX] 生物按压执行异常: %s", exc)
        try:
            await page.mouse.up()
        except Exception:
            pass
        return False


async def solve_perimeterx_gate(
    page: Any,
    context: Any,
    timeout_s: float = 60.0,
    max_attempts: int = 3,
) -> PerimeterXSolution:
    """在现有页面会话中就地解决已渲染的 PerimeterX 挑战 (Inline Solver)"""
    t0 = time.monotonic()
    sol = PerimeterXSolution()

    # 1. 记录按压前的 cookie 状态
    cookies_before = await _extract_px_cookies(context)
    px3_before = cookies_before.get("_px3")

    logger.info("🔍 [PerimeterX] 启动现场检测，初始 _px3: %s", px3_before[:16] + "..." if px3_before else "None")

    deadline = t0 + timeout_s
    while time.monotonic() < deadline and sol.attempts < max_attempts:
        target = await detect_perimeterx_gate(page)
        if not target:
            # 再次确认是否可见
            if not await is_perimeterx_visible(page):
                if sol.press_hold_actuated:
                    # 说明此前已按压且门已消失
                    break
                await asyncio.sleep(1.5)
                continue

        sol.gate_reached = True
        sol.attempts += 1
        logger.info("🚀 [PerimeterX] 尝试第 %d/%d 次长按解密...", sol.attempts, max_attempts)

        if not target:
            # 降级：视口中心偏下尝试
            viewport = await page.evaluate("() => ({w: window.innerWidth, h: window.innerHeight})")
            target = {"x": viewport["w"] * 0.3, "y": viewport["h"] * 0.45, "width": 200, "height": 60}

        actuated = await _biometric_press_hold(page, target)
        if actuated:
            sol.press_hold_actuated = True

        # 等待后端 PoW 计算烘焙与 Cookie 更新 (2~3.5s)
        await asyncio.sleep(random.uniform(2.5, 3.5))

        # 检查挑战是否消失
        if not await is_perimeterx_visible(page):
            logger.info("✨ [PerimeterX] 挑战门已顺利通关关闭！")
            break

    # 2. 收割最新的 Cookie Bundle
    cookies_after = await _extract_px_cookies(context)
    px3_after = cookies_after.get("_px3")
    sol.cookies = cookies_after
    sol.px3 = px3_after
    if cookies_after:
        sol.cookie_header = "; ".join(f"{k}={v}" for k, v in cookies_after.items())

    sol.px3_rotated = bool(px3_before and px3_after and px3_before != px3_after)
    sol.solved = bool(sol.press_hold_actuated and px3_after and (sol.px3_rotated or not px3_before))
    sol.elapsed = round(time.monotonic() - t0, 2)

    try:
        sol.user_agent = await page.evaluate("() => navigator.userAgent")
    except Exception:
        sol.user_agent = None

    sol.replay_contract = {
        "bound_to": ["_pxvid", "client_ip", "user_agent"],
        "ttl_note": "PerimeterX _px3 短效有效 (数分钟至1小时)，需搭配同 IP/UA 重放",
        "replay_cookies": list(cookies_after.keys()),
    }

    if not sol.gate_reached:
        sol.error = "未检测到 PerimeterX 挑战门渲染 (可能已被静默放行)"
    elif not sol.solved:
        sol.error = "长按已触发但 _px3 认证令牌未更新，可能被判定为高风险 IP"

    return sol


async def harvest_perimeterx_cookies(
    url: Optional[str] = None,
    render_flow: str = "outlook_signup",
    proxy: Optional[str] = None,
    headless: bool = True,
    timeout_s: float = 180.0,
) -> PerimeterXSolution:
    """独立凭证收割器 (Stand-alone Clearance Cookie Harvester)"""
    t0 = time.monotonic()
    sol = PerimeterXSolution()

    from .renderers import RENDERERS

    renderer_fn = RENDERERS.get(render_flow)
    if not renderer_fn and not url:
        sol.error = f"未知 render_flow '{render_flow}' 且未提供 direct url"
        return sol

    launch_args = [
        "--disable-blink-features=AutomationControlled",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    proxy_config = None
    if proxy:
        proxy_config = {"server": proxy}

    logger.info("🌐 [PerimeterX Harvester] 正在启动隐身浏览器会话 (Flow: %s)...", render_flow)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=headless,
            args=launch_args,
            proxy=proxy_config,
        )
        context = await browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/133.0.0.0 Safari/537.36",
        )
        page = await context.new_page()

        try:
            if renderer_fn:
                await renderer_fn(page)
            else:
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await asyncio.sleep(4)

            # 调用现场解密核心
            sol = await solve_perimeterx_gate(page, context, timeout_s=max(30.0, timeout_s - (time.monotonic() - t0)))
        except Exception as exc:
            logger.error("❌ [PerimeterX Harvester] 流程异常: %s", exc)
            sol.error = f"Harvest 运行异常: {exc}"
        finally:
            await browser.close()

    sol.elapsed = round(time.monotonic() - t0, 2)
    return sol
