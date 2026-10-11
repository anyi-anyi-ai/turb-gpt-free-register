# -*- coding: utf-8 -*-
"""生物动力学拟人输入引擎 (Biometric Human Dynamics Engine)

深度借鉴与吸收：
1. references/OxyMouse: 五阶段物理运动模型 (加速-逼近-减速-过冲回正-微调) 与生理噪声
2. Keystroke Dynamics 行为生物学: 对数正态击键飞行时延 (Flight Time)、按键驻留时间 (Dwell Time)
3. QWERTY 空间邻近键矩阵与真实认知反应失误-退格纠错机制 (Cognitive Reaction Typos & Backspace)

跨驱动支持: Playwright / Patchright / nodriver
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from typing import Any, List, Tuple

import numpy as np


# -----------------------------------------------------------------------------
# QWERTY 物理键盘邻近矩阵 (用于生成真实人手按错键与认知回退)
# -----------------------------------------------------------------------------
QWERTY_NEIGHBORS = {
    'q': ['w', 'a', 's', '1', '2'],
    'w': ['q', 'e', 'a', 's', 'd', '2', '3'],
    'e': ['w', 'r', 's', 'd', 'f', '3', '4'],
    'r': ['e', 't', 'd', 'f', 'g', '4', '5'],
    't': ['r', 'y', 'f', 'g', 'h', '5', '6'],
    'y': ['t', 'u', 'g', 'h', 'j', '6', '7'],
    'u': ['y', 'i', 'h', 'j', 'k', '7', '8'],
    'i': ['u', 'o', 'j', 'k', 'l', '8', '9'],
    'o': ['i', 'p', 'k', 'l', '9', '0'],
    'p': ['o', 'l', '0', '-', '['],
    'a': ['q', 'w', 's', 'z'],
    's': ['a', 'w', 'e', 'd', 'x', 'z'],
    'd': ['s', 'e', 'r', 'f', 'c', 'x'],
    'f': ['d', 'r', 't', 'g', 'v', 'c'],
    'g': ['f', 't', 'y', 'h', 'b', 'v'],
    'h': ['g', 'y', 'u', 'j', 'n', 'b'],
    'j': ['h', 'u', 'i', 'k', 'm', 'n'],
    'k': ['j', 'i', 'o', 'l', 'm'],
    'l': ['k', 'o', 'p', ';'],
    'z': ['a', 's', 'x'],
    'x': ['z', 's', 'd', 'c'],
    'c': ['x', 'd', 'f', 'v'],
    'v': ['c', 'f', 'g', 'b'],
    'b': ['v', 'g', 'h', 'n'],
    'n': ['b', 'h', 'j', 'm'],
    'm': ['n', 'j', 'k'],
    '1': ['2', 'q'],
    '2': ['1', '3', 'q', 'w'],
    '3': ['2', '4', 'w', 'e'],
    '4': ['3', '5', 'e', 'r'],
    '5': ['4', '6', 'r', 't'],
    '6': ['5', '7', 't', 'y'],
    '7': ['6', '8', 'y', 'u'],
    '8': ['7', '9', 'u', 'i'],
    '9': ['8', '0', 'i', 'o'],
    '0': ['9', '-', 'o', 'p'],
}


class BiometricMouse:
    """基于 OxyMouse 运动学改良的物理拟人鼠标引擎"""

    @staticmethod
    def generate_path(
        from_pt: Tuple[float, float],
        to_pt: Tuple[float, float],
        steps: int = 40,
        overshoot: bool = True,
    ) -> List[Tuple[int, int]]:
        """生成带加速、减速、微冲与生理颤抖的自然坐标序列。"""
        x1, y1 = from_pt
        x2, y2 = to_pt
        distance = math.hypot(x2 - x1, y2 - y1)
        if distance < 4:
            return [(int(x2), int(y2))]

        # 步数根据距离自适应，保证平滑度
        steps = max(25, min(80, int(distance / 12) + random.randint(5, 15)))

        # 生成贝塞尔控制点，并带随机横向拉力
        mid_x = (x1 + x2) / 2.0
        mid_y = (y1 + y2) / 2.0
        pull_factor = random.uniform(-0.25, 0.25)
        ctrl_x = mid_x - (y2 - y1) * pull_factor + random.uniform(-20, 20)
        ctrl_y = mid_y + (x2 - x1) * pull_factor + random.uniform(-20, 20)

        # 若启用过冲 (Overshoot)，目标点稍作偏移，最后一步拉回
        target_x, target_y = x2, y2
        if overshoot and distance > 80 and random.random() < 0.7:
            angle = math.atan2(y2 - y1, x2 - x1)
            over_dist = random.uniform(4, min(18, distance * 0.08))
            target_x = x2 + math.cos(angle) * over_dist
            target_y = y2 + math.sin(angle) * over_dist

        points = []
        # 五阶段参数化速度权重 (Sigmoid-like 变速曲线)
        for i in range(steps):
            t = i / float(steps - 1)
            # Ease-in-out 加速度映射
            eased_t = t * t * (3.0 - 2.0 * t)

            # 二阶贝塞尔基础轨迹
            bx = (1 - eased_t) ** 2 * x1 + 2 * (1 - eased_t) * eased_t * ctrl_x + eased_t ** 2 * target_x
            by = (1 - eased_t) ** 2 * y1 + 2 * (1 - eased_t) * eased_t * ctrl_y + eased_t ** 2 * target_y

            # 叠加生理肌肉微颤 (High-frequency Gaussian Tremor)
            tremor_amp = math.sin(t * math.pi) * 2.2  # 轨迹中段晃动稍大，端点归零
            noise_x = random.gauss(0, 0.7) * tremor_amp
            noise_y = random.gauss(0, 0.7) * tremor_amp

            points.append((int(round(bx + noise_x)), int(round(by + noise_y))))

        # 若发生了过冲，增加 3~6 步微调对正 (Micro-correction settling)
        if overshoot and (target_x != x2 or target_y != y2):
            cur_x, cur_y = points[-1]
            settle_steps = random.randint(4, 7)
            for j in range(1, settle_steps + 1):
                st = j / float(settle_steps)
                sx = cur_x + (x2 - cur_x) * st + random.gauss(0, 0.4)
                sy = cur_y + (y2 - cur_y) * st + random.gauss(0, 0.4)
                points.append((int(round(sx)), int(round(sy))))

        points.append((int(round(x2)), int(round(y2))))
        return points

    @classmethod
    async def move_playwright(cls, page: Any, target_x: float, target_y: float) -> None:
        """在 Playwright/Patchright 页面上执行生物拟人平滑移动 (异步)"""
        cur = getattr(page, "_bio_mouse_pos", None)
        if not isinstance(cur, (tuple, list)) or len(cur) != 2:
            cur = (random.randint(50, 200), random.randint(50, 200))
        path = cls.generate_path(cur, (target_x, target_y))
        for px, py in path:
            await page.mouse.move(px, py)
            await asyncio.sleep(random.uniform(0.002, 0.006))
        page._bio_mouse_pos = (target_x, target_y)

    @classmethod
    def move_playwright_sync(cls, page: Any, target_x: float, target_y: float) -> None:
        """在 Playwright/Patchright 页面上执行生物拟人平滑移动 (同步)"""
        cur = getattr(page, "_bio_mouse_pos", None)
        if not isinstance(cur, (tuple, list)) or len(cur) != 2:
            cur = (random.randint(50, 200), random.randint(50, 200))
        path = cls.generate_path(cur, (target_x, target_y))
        for px, py in path:
            page.mouse.move(px, py)
            time.sleep(random.uniform(0.002, 0.006))
        page._bio_mouse_pos = (target_x, target_y)

    @classmethod
    async def move_nodriver(cls, tab: Any, target_x: float, target_y: float) -> None:
        """在 nodriver (原生 Chromium CDP) 页面上执行生物拟人平滑移动"""
        import nodriver.cdp.input_ as cdp_input

        cur = getattr(tab, "_bio_mouse_pos", None)
        if not isinstance(cur, (tuple, list)) or len(cur) != 2:
            cur = (random.randint(50, 200), random.randint(50, 200))
        path = cls.generate_path(cur, (target_x, target_y))
        for px, py in path:
            await tab.send(
                cdp_input.dispatch_mouse_event(
                    type_="mouseMoved",
                    x=float(px),
                    y=float(py),
                )
            )
            await asyncio.sleep(random.uniform(0.002, 0.006))
        tab._bio_mouse_pos = (target_x, target_y)

    @classmethod
    async def click_playwright(cls, page: Any, locator: Any) -> None:
        """带拟人贝塔分布落点与停留时延的真实点击 (Playwright 异步)"""
        box = await locator.bounding_box()
        if not box:
            await locator.click()
            return
        # 贝塔分布偏置，人手通常点击在元素偏中上/中左位置，绝不会每次精确落在绝对中心
        offset_x = box["width"] * random.betavariate(2.5, 2.5)
        offset_y = box["height"] * random.betavariate(2.5, 2.5)
        target_x = box["x"] + offset_x
        target_y = box["y"] + offset_y

        await cls.move_playwright(page, target_x, target_y)
        await asyncio.sleep(random.uniform(0.04, 0.11))  # 悬停准备
        await page.mouse.down()
        await asyncio.sleep(random.uniform(0.06, 0.12))  # 按压持续
        await page.mouse.up()

    @classmethod
    def click_playwright_sync(cls, page: Any, locator: Any) -> None:
        """带拟人贝塔分布落点与停留时延的真实点击 (Playwright 同步)"""
        box = locator.bounding_box()
        if not box:
            locator.click()
            return
        offset_x = box["width"] * random.betavariate(2.5, 2.5)
        offset_y = box["height"] * random.betavariate(2.5, 2.5)
        target_x = box["x"] + offset_x
        target_y = box["y"] + offset_y

        cls.move_playwright_sync(page, target_x, target_y)
        time.sleep(random.uniform(0.04, 0.11))
        page.mouse.down()
        time.sleep(random.uniform(0.06, 0.12))
        page.mouse.up()

    @classmethod
    async def click_nodriver(cls, tab: Any, element: Any) -> None:
        """带拟人轨迹与物理时延的真实点击 (nodriver)"""
        import nodriver.cdp.input_ as cdp_input

        # 获取元素中心坐标并增加随机偏置
        box = getattr(element, "center", None)
        if not box:
            await element.click()
            return
        cx, cy = box
        target_x = cx + random.gauss(0, 3.5)
        target_y = cy + random.gauss(0, 3.5)

        await cls.move_nodriver(tab, target_x, target_y)
        await asyncio.sleep(random.uniform(0.04, 0.11))
        await tab.send(
            cdp_input.dispatch_mouse_event(
                type_="mousePressed",
                x=float(target_x),
                y=float(target_y),
                button=cdp_input.MouseButton.LEFT,
                click_count=1,
            )
        )
        await asyncio.sleep(random.uniform(0.06, 0.12))
        await tab.send(
            cdp_input.dispatch_mouse_event(
                type_="mouseReleased",
                x=float(target_x),
                y=float(target_y),
                button=cdp_input.MouseButton.LEFT,
                click_count=1,
            )
        )


class BiometricKeyboard:
    """对数正态分布与认知失误纠错拟人击键引擎"""

    @staticmethod
    def get_dwell_time() -> float:
        """手指按键驻留时长 (Key-down 到 Key-up，均值约 85ms，正态分布)"""
        val = random.gauss(0.085, 0.018)
        return max(0.045, min(0.160, val))

    @staticmethod
    def get_flight_time() -> float:
        """字符间移动间隔时长 (Flight time，对数正态分布，长尾模拟轻微迟疑)"""
        # 对数正态分布: 中位数约 125ms，右侧长尾偶尔达到 250~400ms
        val = np.random.lognormal(mean=-2.1, sigma=0.42)
        return max(0.060, min(0.600, float(val)))

    @classmethod
    async def type_playwright(
        cls,
        page: Any,
        selector: str,
        text: str,
        typo_rate: float = 0.02,
    ) -> None:
        """以真实人类生物动力学在 Playwright 元素中敲击文本 (支持退格纠错)"""
        element = page.locator(selector).first
        await element.click()
        await asyncio.sleep(random.uniform(0.12, 0.28))  # 点击后聚焦停顿

        for char in text:
            # 模拟按错邻近键 + 退格修复
            lower_char = char.lower()
            if typo_rate > 0 and lower_char in QWERTY_NEIGHBORS and random.random() < typo_rate:
                typo_char = random.choice(QWERTY_NEIGHBORS[lower_char])
                # 按下错键
                await page.keyboard.press(typo_char, delay=int(cls.get_dwell_time() * 1000))
                # 人脑发现错误的认知反应延迟 (220ms ~ 450ms)
                await asyncio.sleep(random.uniform(0.22, 0.45))
                # 按下 Backspace 修正
                await page.keyboard.press("Backspace", delay=int(cls.get_dwell_time() * 1000))
                # 重新聚焦短暂延迟
                await asyncio.sleep(random.uniform(0.08, 0.18))

            # 正常击键
            dwell_ms = int(cls.get_dwell_time() * 1000)
            await page.keyboard.press(char, delay=dwell_ms)

            # 字符间飞行时延
            await asyncio.sleep(cls.get_flight_time())

    @classmethod
    def type_playwright_sync(
        cls,
        page: Any,
        text: str,
        typo_rate: float = 0.02,
    ) -> None:
        """以真实人类生物动力学在 Playwright 同步页面上敲击文本 (支持退格纠错)"""
        time.sleep(random.uniform(0.12, 0.28))

        for char in text:
            lower_char = char.lower()
            if typo_rate > 0 and lower_char in QWERTY_NEIGHBORS and random.random() < typo_rate:
                typo_char = random.choice(QWERTY_NEIGHBORS[lower_char])
                page.keyboard.press(typo_char, delay=int(cls.get_dwell_time() * 1000))
                time.sleep(random.uniform(0.22, 0.45))
                page.keyboard.press("Backspace", delay=int(cls.get_dwell_time() * 1000))
                time.sleep(random.uniform(0.08, 0.18))

            dwell_ms = int(cls.get_dwell_time() * 1000)
            page.keyboard.press(char, delay=dwell_ms)
            time.sleep(cls.get_flight_time())

    @classmethod
    async def type_nodriver(
        cls,
        tab: Any,
        element: Any,
        text: str,
        typo_rate: float = 0.02,
    ) -> None:
        """以真实人类生物动力学在 nodriver 元素中敲击文本 (支持退格纠错)"""
        import nodriver.cdp.input_ as cdp_input

        await element.click()
        await asyncio.sleep(random.uniform(0.12, 0.28))

        for char in text:
            lower_char = char.lower()
            if typo_rate > 0 and lower_char in QWERTY_NEIGHBORS and random.random() < typo_rate:
                typo_char = random.choice(QWERTY_NEIGHBORS[lower_char])
                await element.send_keys(typo_char)
                await asyncio.sleep(random.uniform(0.22, 0.45))
                # 原生 CDP 退格键事件模拟
                try:
                    await tab.send(
                        cdp_input.dispatch_key_event(
                            type_="rawKeyDown",
                            key="Backspace",
                            code="Backspace",
                            windows_virtual_key_code=8,
                        )
                    )
                    await tab.send(
                        cdp_input.dispatch_key_event(
                            type_="keyUp",
                            key="Backspace",
                            code="Backspace",
                            windows_virtual_key_code=8,
                        )
                    )
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(0.08, 0.18))

            # 正常打字
            await element.send_keys(char)
            await asyncio.sleep(cls.get_flight_time())
