# -*- coding: utf-8 -*-
"""PerimeterX / HUMAN Gate Detection Module

多维度感知检测：
1. 页面与各类多重嵌套 iframe (hsprotect.net, px-captcha, perimeterx)
2. 多语言文本触发词 (Press and hold / 按住 / 长按 / Prove you're human / Halten / Appuyer et maintenir)
3. 视口内有效性与物理渲染尺寸校验 (Bounding Box Check)
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("perimeterx_detector")

PX_IFRAME_PATTERNS = ["hsprotect.net", "perimeterx", "px-captcha"]
PX_TEXT_TRIGGERS = [
    "press and hold",
    "press & hold",
    "appuyer et maintenir",
    "按住",
    "长按",
    "halten",
    "prove you're human",
    "prove you are human",
]


async def _get_viewport(page: Any) -> Optional[Tuple[float, float]]:
    try:
        val = await page.evaluate("() => ({width: window.innerWidth, height: window.innerHeight})")
        if isinstance(val, dict):
            w = float(val.get("width") or 0)
            h = float(val.get("height") or 0)
            if w > 0 and h > 0:
                return w, h
    except Exception:
        pass
    return None


def is_box_visible(box: Optional[Dict[str, float]], viewport: Optional[Tuple[float, float]],
                   min_w: float = 30, min_h: float = 8) -> bool:
    if not box or box.get("width", 0) < min_w or box.get("height", 0) < min_h:
        return False
    if not viewport:
        return box.get("x", 0) >= 0 and box.get("y", 0) >= 0
    w, h = viewport
    cx = float(box.get("x", 0)) + float(box.get("width", 0)) / 2
    cy = float(box.get("y", 0)) + float(box.get("height", 0)) / 2
    return 0 <= cx < w and 0 <= cy < h


async def is_perimeterx_visible(page: Any) -> bool:
    """快速判定当前页面是否存在处于激活状态的 PerimeterX / HUMAN 挑战拦截门"""
    try:
        # 1. 检查页面内部 JavaScript 扫描评估
        visible_js = await page.evaluate(r"""() => {
            const iframes = [...document.querySelectorAll('iframe')];
            const pxFrame = iframes.find(f => {
                const s = f.src || '';
                return /hsprotect|perimeterx|px-captcha/i.test(s) && f.getBoundingClientRect().width > 40;
            });
            const text = (document.body ? document.body.innerText : '').toLowerCase();
            const textMatched = /press *(&|and) *hold|prove you('re| are) human|按住|长按/i.test(text);
            return !!(pxFrame || textMatched);
        }""")
        if visible_js:
            return True

        # 2. 定位具体的 Playwright locator 探测
        viewport = await _get_viewport(page)
        for sel in [
            'button:has-text("Press and hold")',
            'button:has-text("按住")',
            'button:has-text("长按")',
            'button:has-text("Appuyer et maintenir")',
            'button:has-text("Halten")',
            '#px-captcha',
            'iframe[src*="hsprotect.net"]',
            'iframe[src*="px-captcha"]',
        ]:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                box = await loc.bounding_box()
                if is_box_visible(box, viewport):
                    return True
    except Exception:
        pass
    return False


async def detect_perimeterx_gate(page: Any) -> Optional[Dict[str, Any]]:
    """定位 PerimeterX 物理交互目标，返回精确坐标与类型元数据。
    
    返回字典结构:
    {
        "x": float,
        "y": float,
        "width": float,
        "height": float,
        "is_button": bool,
        "frame_url": str
    }
    """
    viewport = await _get_viewport(page)

    # 1. 深度检测子 frame 内的 #px-captcha 交互按钮
    refresh_frames = getattr(page, "_refresh_frames", None)
    if refresh_frames:
        try:
            await refresh_frames(force=True)
        except Exception:
            pass

    for frame in getattr(page, "frames", []):
        if frame == page.main_frame:
            continue
        frame_url = getattr(frame, "url", "") or ""
        if any(pat in frame_url.lower() for pat in PX_IFRAME_PATTERNS):
            try:
                btn = frame.locator("#px-captcha, button").first
                if await btn.count() > 0:
                    box = await btn.bounding_box()
                    if is_box_visible(box, viewport, min_w=25, min_h=8):
                        return {
                            "x": box["x"],
                            "y": box["y"],
                            "width": box["width"],
                            "height": box["height"],
                            "is_button": True,
                            "frame_url": frame_url,
                        }
            except Exception:
                pass

    # 2. 检测顶层主 DOM 中的 iframe 容器区域
    for pat in PX_IFRAME_PATTERNS:
        loc = page.locator(f'iframe[src*="{pat}"]').first
        try:
            if await loc.count() > 0:
                box = await loc.bounding_box()
                if is_box_visible(box, viewport, min_w=50, min_h=30):
                    return {
                        "x": box["x"],
                        "y": box["y"],
                        "width": box["width"],
                        "height": box["height"],
                        "is_button": False,
                        "frame_url": pat,
                    }
        except Exception:
            pass

    # 3. 页面内直接暴露的按钮
    for sel in [
        'button:has-text("Press and hold")',
        'button:has-text("按住")',
        'button:has-text("长按")',
        '#px-captcha',
    ]:
        loc = page.locator(sel).first
        try:
            if await loc.count() > 0:
                box = await loc.bounding_box()
                if is_box_visible(box, viewport, min_w=30, min_h=10):
                    return {
                        "x": box["x"],
                        "y": box["y"],
                        "width": box["width"],
                        "height": box["height"],
                        "is_button": True,
                        "frame_url": "",
                    }
        except Exception:
            pass

    return None
