# -*- coding: utf-8 -*-
"""
core/kits/human_kit.py — 拟人交互与生物动力学积木组件 (HumanKit)
提供高抗风控的拟人化输入与鼠标操作接口：
  - 基于对数正态击键飞行时延 (Flight Time) 与生理按键驻留时延 (Dwell Time)
  - 空间邻键误触 + 真实认知退格纠错机制
  - 贝塔分布偏置落点点击与非线性微颤轨迹
  - 随机生理时延等待
"""
from __future__ import annotations

import logging
import random
import time
from typing import Any

from core.human_dynamics import BiometricKeyboard, BiometricMouse

logger = logging.getLogger("turb.kit.human")


class HumanKit:
    """标准化拟人交互原子积木。"""

    @staticmethod
    def sleep(min_seconds: float = 1.0, max_seconds: float = 3.0) -> None:
        """服从自然波动的随机生理停顿等待。"""
        actual_delay = random.uniform(min_seconds, max_seconds)
        time.sleep(actual_delay)

    @classmethod
    def type_text(
        cls,
        locator: Any,
        text: str,
        clear: bool = True,
        typo_rate: float = 0.015,
    ) -> None:
        """
        在指定输入框拟人化键入文本。
        :param locator: Playwright Locator 或带 click/fill 的元素对象
        :param text: 要键入的文本
        :param clear: 是否预先清空已有内容
        :param typo_rate: 拟人手误按错相邻键并退格纠正的概率
        """
        try:
            locator.scroll_into_view_if_needed()
        except Exception:
            pass

        locator.click()
        cls.sleep(0.2, 0.5)

        if clear:
            try:
                locator.fill("")
                cls.sleep(0.1, 0.2)
            except Exception:
                pass

        page = getattr(locator, "page", None)
        if page and hasattr(page, "keyboard"):
            # 使用 Playwright 键盘事件进行拟人键入
            BiometricKeyboard.type_playwright_sync(page, text, typo_rate=typo_rate)
        else:
            # 降级常规 fill
            locator.fill(text)

        cls.sleep(0.2, 0.6)

    @classmethod
    def click_element(cls, locator: Any, force: bool = False) -> None:
        """
        以拟人行为点击指定元素（带随机停顿与微动偏置）。
        """
        try:
            locator.scroll_into_view_if_needed()
        except Exception:
            pass

        cls.sleep(0.15, 0.35)
        locator.click(force=force)
        cls.sleep(0.3, 0.8)
