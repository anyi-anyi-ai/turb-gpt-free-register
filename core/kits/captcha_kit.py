# -*- coding: utf-8 -*-
"""
core/kits/captcha_kit.py — 验证码攻防与人机协同积木组件 (CaptchaKit)
提供全矩阵的打码穿透与协同能力：
  - Cloudflare Turnstile / 5 秒盾智能穿透与自动点选
  - Arkose / hCaptcha 人机协同拼图极速通关监听 (等待人工解决或轮询 DOM 标记)
  - 本地 Whisper 语音验证码听力降维 (可选)
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Optional

from core.cloudflare_turnstile import (
    is_cloudflare_challenge,
    solve_or_wait_cloudflare_turnstile,
)

logger = logging.getLogger("turb.kit.captcha")


class CaptchaKit:
    """标准化验证码穿透与协同原子积木。"""

    @staticmethod
    def handle_turnstile(driver: Any, timeout: float = 30.0) -> bool:
        """
        检测并穿透 Cloudflare Turnstile 或 5 秒盾质询。
        """
        if is_cloudflare_challenge(driver):
            logger.info("[CaptchaKit] 检测到 Cloudflare 质询，开始自动穿透...")
            ok = solve_or_wait_cloudflare_turnstile(driver, timeout=timeout)
            time.sleep(1.5)
            return bool(ok)
        return True

    @staticmethod
    def wait_human_puzzle(
        driver: Any,
        check_passed_fn: Optional[Callable[[], bool]] = None,
        hint: str = "检测到拼图/图形验证码，请在浏览器实时视窗中完成拼图/滑块验证...",
        max_wait: int = 180,
        poll_interval: float = 1.5,
        screencast: bool = True,
    ) -> bool:
        """
        针对高风控平台（如 RunningHub 旋转滑块、ElevenLabs Arkose 动物旋转、GitHub 拼图）的人机零侵入协同接口。
        特性：
          1. 自动挂载 CDP 实时推流视窗至本地系统默认浏览器 (http://127.0.0.1:8799/live)。
          2. 解决 Windows 桌面原生抢焦弹窗失败与无头环境痛点，用户直接在网页画布上拖拽交互。
          3. 实时双通道通关探测（DOM 标记检测 + 控制台敲击回车即刻强制接管）。
        :param driver: 浏览器驱动对象 (CloakBrowser, Playwright Page 或带有 page 属性的容器)
        :param check_passed_fn: 自定义判断拼图是否通关的回调函数
        :param hint: 提示信息
        :param max_wait: 最大等待秒数
        :param poll_interval: 轮询检查间隔
        :param screencast: 是否自动开启 Web 投屏与弹出默认浏览器
        """
        import sys
        hub = None
        page = getattr(driver, "page", driver)

        if screencast and page:
            try:
                from core.live_browser_hub import LiveBrowserHub
                hub = LiveBrowserHub.get_hub()
                hub.ensure_server(8799)
                hub.attach_page(page, auto_open=True, port=8799)
            except Exception as e:
                logger.debug(f"[CaptchaKit] 挂载 LiveBrowserHub 视窗异常: {e}")

        logger.warning("=" * 72)
        logger.warning("🚀 [CaptchaKit] 人机零侵入 Web 协同已激活！")
        logger.warning(f"   • 提示说明: {hint}")
        logger.warning("   • 视窗地址: http://127.0.0.1:8799/live (已在默认浏览器自动打开)")
        logger.warning("   • 交互方式: 请直接在浏览器标签页的画面中拖动滑块或点击旋转！")
        logger.warning("   • 智能接管: 通关后脚本自动无缝推进；亦可在终端按【回车键 (Enter)】强制接管。")
        logger.warning(f"   • 最大等待: {max_wait} 秒")
        logger.warning("=" * 72)

        has_msvcrt = False
        if sys.platform.startswith("win"):
            try:
                import msvcrt
                has_msvcrt = True
            except Exception:
                pass

        deadline = time.time() + max_wait
        try:
            while time.time() < deadline:
                # 1. 终端按键强制接管探测
                if has_msvcrt and msvcrt.kbhit():
                    ch = msvcrt.getch()
                    if ch in (b'\r', b'\n'):
                        logger.info("[CaptchaKit] ⌨️ 控制台捕获到回车键，用户触发强制接管！")
                        return True

                # 2. 自定义通关条件检测
                try:
                    if check_passed_fn and check_passed_fn():
                        logger.info("[CaptchaKit] ✨ 探测到拼图验证码已通关！脚本自动无缝接管！")
                        return True
                except Exception:
                    pass

                time.sleep(poll_interval)

            logger.error(f"[CaptchaKit] 人机协同拼图等待超时 ({max_wait}s)")
            return False
        finally:
            if hub:
                try:
                    hub.detach_page()
                except Exception:
                    pass

    @staticmethod
    def solve_perimeterx(page: Any, timeout: float = 20.0) -> bool:
        """
        检测并自动穿透 PerimeterX / HUMAN Security 'Press & Hold' 挑战门。
        结合 BiometricMouse 施加 8~12Hz 微生理震颤与亚像素漂移。
        """
        try:
            from core.perimeterx import solve_perimeterx_gate
            return bool(solve_perimeterx_gate(page, timeout=timeout))
        except Exception as e:
            logger.debug(f"[CaptchaKit] PerimeterX 求解器调用异常: {e}")
            return False

    @staticmethod
    def solve_whisper_audio(page: Any) -> Optional[str]:
        """
        从页面无障碍语音通道内生拉取音频流，并通过本地 Whisper INT8 模型极速听力转录。
        """
        try:
            from core.audio_solver.solver import solve_audio_challenge
            return solve_audio_challenge(page)
        except Exception as e:
            logger.debug(f"[CaptchaKit] Whisper 语音打码调用异常: {e}")
            return None

    @staticmethod
    def solve_vision(page: Any, preset_or_spec: str) -> bool:
        """
        调用视觉验证码求解引擎（支持本地 ONNX / 端侧 VLM / 云端多模型投票）。
        """
        try:
            from core.vision_solver import solve, CaptchaSpec
            spec = CaptchaSpec.from_json(preset_or_spec)
            import asyncio
            if asyncio.iscoroutinefunction(solve):
                import asyncio
                loop = asyncio.get_event_loop()
                return loop.run_until_complete(solve(page, spec))
            return bool(solve(page, spec))
        except Exception as e:
            logger.debug(f"[CaptchaKit] 视觉验证码求解器调用异常: {e}")
            return False

