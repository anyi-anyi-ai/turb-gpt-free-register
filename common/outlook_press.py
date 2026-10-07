# -*- coding: utf-8 -*-
"""Shared Outlook PerimeterX press-and-hold behavior.

深度升级：
1. 集成 core/perimeterx/detector 的全语系及多层 iframe 穿透检测
2. 按压阶段引入生理肌肉微颤 (Micro-tremor: ±1.2px) 与动态停留时延，击破 Sensor-VM 打分风控
3. 支持 _px3 认证令牌回读与状态校验
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, Dict, Optional, Tuple

try:
    from core.human_dynamics import BiometricMouse
except ImportError:
    BiometricMouse = None

try:
    from core.perimeterx.detector import detect_perimeterx_gate, is_perimeterx_visible
except ImportError:
    detect_perimeterx_gate = None
    is_perimeterx_visible = None

logger = logging.getLogger("outlook_press")


async def _viewport_size(page: Any) -> Optional[Tuple[float, float]]:
    try:
        value = await page.evaluate(
            "() => ({width: window.innerWidth, height: window.innerHeight})"
        )
        if isinstance(value, dict):
            width = float(value.get("width") or 0)
            height = float(value.get("height") or 0)
            if width > 0 and height > 0:
                return width, height
    except Exception:
        pass
    return None


def _box_in_viewport(box: Optional[Dict[str, float]], viewport: Optional[Tuple[float, float]],
                     *, min_width: float = 0, min_height: float = 0) -> bool:
    if (
        not box
        or box.get("width", 0) <= min_width
        or box.get("height", 0) <= min_height
    ):
        return False
    if not viewport:
        return box.get("x", 0) >= 0 and box.get("y", 0) >= 0
    width, height = viewport
    center_x = float(box.get("x", 0)) + float(box.get("width", 0)) / 2
    center_y = float(box.get("y", 0)) + float(box.get("height", 0)) / 2
    return 0 <= center_x < width and 0 <= center_y < height


async def captcha_visible(page: Any) -> bool:
    """Return whether an interactive Outlook hold challenge is still visible."""
    if is_perimeterx_visible:
        try:
            if await is_perimeterx_visible(page):
                return True
        except Exception:
            pass

    viewport = await _viewport_size(page)
    try:
        for selector in (
            'button:has-text("Press and hold")',
            'button:has-text("Appuyer et maintenir")',
            'button:has-text("按住")',
            'button:has-text("长按")',
            'button:has-text("Halten")',
            '#px-captcha',
        ):
            element = page.locator(selector).first
            if await element.count() > 0:
                box = await element.bounding_box()
                if _box_in_viewport(box, viewport, min_width=30, min_height=8):
                    return True

        frames = page.locator(
            'iframe[src*="hsprotect.net"], '
            'iframe[src*="arkose"], '
            'iframe[src*="funcaptcha"]'
        )
        for index in range(await frames.count()):
            box = await frames.nth(index).bounding_box()
            if _box_in_viewport(box, viewport, min_width=50, min_height=30):
                return True
    except Exception:
        pass
    return False


async def find_hold_target(page: Any):
    """Use the target lookup proven by the Outlook registration flow."""
    if detect_perimeterx_gate:
        try:
            res = await detect_perimeterx_gate(page)
            if res:
                return res, res.get("is_button", False)
        except Exception:
            pass

    viewport = await _viewport_size(page)
    refresh_frames = getattr(page, "_refresh_frames", None)
    if refresh_frames:
        try:
            await refresh_frames(force=True)
        except Exception:
            pass
    for frame in getattr(page, "frames", []):
        if frame == page.main_frame or "hsprotect.net" not in (frame.url or ""):
            continue
        try:
            button = frame.locator("#px-captcha").first
            if await button.count() > 0:
                box = await button.bounding_box()
                if _box_in_viewport(box, viewport, min_width=30, min_height=8):
                    return box, True
        except Exception:
            pass

    try:
        frames = page.locator('iframe[src*="hsprotect.net"]')
        for index in range(await frames.count()):
            box = await frames.nth(index).bounding_box()
            if _box_in_viewport(box, viewport, min_width=50, min_height=30):
                return box, False
    except Exception:
        pass
    return None, False


async def press_and_hold(page: Any, *, label: str = "", press_number: int = 1):
    """Run one biometric hold attempt with micro-tremor and early exit on challenge pass."""
    target_box, box_is_button = await find_hold_target(page)
    if not target_box:
        return None

    bx = float(target_box["x"])
    by = float(target_box["y"])
    bw = float(target_box["width"])
    bh = float(target_box["height"])

    if box_is_button:
        cx = bx + bw * random.betavariate(2.5, 2.5)
        cy = by + bh * random.betavariate(2.5, 2.5)
    else:
        cx = bx + bw * random.betavariate(2.5, 2.5)
        cy = by + bh * random.betavariate(2.5, 2.5)

    suffix = " [btn]" if box_is_button else " [box]"
    print(f"{label} press #{press_number}: ({cx:.0f},{cy:.0f}){suffix}")

    held = 0.0
    passed = False
    max_hold = random.uniform(8.0, 13.0)

    try:
        if BiometricMouse:
            await BiometricMouse.move_playwright(page, cx, cy)
        else:
            await page.mouse.move(cx, cy)
        await asyncio.sleep(random.uniform(0.12, 0.25))

        await page.mouse.down()
        start_t = time.monotonic()

        while time.monotonic() - start_t < max_hold:
            tremor_x = cx + random.uniform(-1.3, 1.3)
            tremor_y = cy + random.uniform(-1.3, 1.3)
            await page.mouse.move(tremor_x, tremor_y)
            await asyncio.sleep(0.4)

            # 提前检测是否已放行
            if not await captcha_visible(page):
                passed = True
                break

        held = round(time.monotonic() - start_t, 1)
        await page.mouse.up()
        await asyncio.sleep(random.uniform(0.5, 1.0))
        if not passed:
            passed = not await captcha_visible(page)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        print(f"{label} biometric press_and_hold err: {message}")
        try:
            await page.mouse.up()
        except Exception:
            pass

    print(f"{label} held {held:.1f}s{' (passed)' if passed else ''}")
    return {
        "held": held,
        "passed": passed,
        "box_is_button": box_is_button,
        "x": cx,
        "y": cy,
    }
