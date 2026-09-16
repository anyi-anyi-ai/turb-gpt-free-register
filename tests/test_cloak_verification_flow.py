# -*- coding: utf-8 -*-
"""测试 CloakBrowser 默认无头、自动清障、切换有头人工接管、超时放弃与释放邮箱的完整流程。"""
import unittest
from unittest.mock import MagicMock, patch

from config import cloakbrowser as _cfg
from core.cloudflare_turnstile import (
    VerificationTimeoutError,
    is_cloudflare_challenge,
    solve_or_wait_cloudflare_turnstile,
)


class MockDriver:
    def __init__(self, url="https://chatgpt.com/auth/login", title="ChatGPT", is_challenge=False, is_headless=True):
        self.current_url = url
        self.title = title
        self._is_challenge = is_challenge
        self._is_headless = is_headless
        self._registration_log_prefix = "[CloakTest]"
        self.page = MagicMock()
        self.page.title.return_value = title
        self.page.frames = []
        self.context = MagicMock()
        self.quit_called = False

    def execute_script(self, script, *args):
        if self._is_challenge:
            return {"isChallenge": True, "reason": "has_challenge_stage"}
        return {"isChallenge": False, "reason": "has_visible_input"}

    def quit(self):
        self.quit_called = True


class TestCloakVerificationFlow(unittest.TestCase):
    def setUp(self):
        import importlib
        from config import env_loader
        env_loader.load_env(override=True)
        importlib.reload(_cfg)
        # 保存原始配置
        self._orig_headless = getattr(_cfg, "CLOAK_HEADLESS", True)
        self._orig_auto_clear = getattr(_cfg, "CLOAK_AUTO_CLEAR_MAX_ATTEMPTS", 2)
        self._orig_timeout = getattr(_cfg, "CLOAK_HUMAN_TAKEOVER_TIMEOUT", 30)

    def tearDown(self):
        # 恢复原始配置
        _cfg.CLOAK_HEADLESS = self._orig_headless
        _cfg.CLOAK_AUTO_CLEAR_MAX_ATTEMPTS = self._orig_auto_clear
        _cfg.CLOAK_HUMAN_TAKEOVER_TIMEOUT = self._orig_timeout

    def test_default_config_values(self):
        """验证默认配置符合需求：默认无头、自动清障2次、接管超时在20~40秒范围内。"""
        import os, importlib
        with patch.dict(os.environ, {
            "CLOAK_HEADLESS": "",
            "CLOAK_AUTO_CLEAR_MAX_ATTEMPTS": "",
            "CLOAK_HUMAN_TAKEOVER_TIMEOUT": "",
        }, clear=False):
            importlib.reload(_cfg)
            self.assertTrue(getattr(_cfg, "CLOAK_HEADLESS", True))
            self.assertGreaterEqual(getattr(_cfg, "CLOAK_AUTO_CLEAR_MAX_ATTEMPTS", 2), 1)
            timeout = getattr(_cfg, "CLOAK_HUMAN_TAKEOVER_TIMEOUT", 30)
            self.assertGreaterEqual(timeout, 20)
            self.assertLessEqual(timeout, 40)

    @patch("core.cloudflare_turnstile.try_click_turnstile_cross_origin")
    def test_auto_clear_success_remains_headless(self, mock_click):
        """场景一：无头状态下遇到人机验证，自动清障成功，不需要重启有头浏览器。"""
        driver = MockDriver(title="Just a moment...", is_challenge=True, is_headless=True)
        relaunch_called = []

        def on_relaunch():
            relaunch_called.append(True)
            driver._is_headless = False
            return driver

        def mock_click_impl(d):
            # 模拟第1次尝试穿透点击成功，盾解除
            d._is_challenge = False
            d.title = "ChatGPT"
            d.page.title.return_value = "ChatGPT"
            return True

        mock_click.side_effect = mock_click_impl

        res = solve_or_wait_cloudflare_turnstile(
            driver,
            timeout=5,
            max_auto_clicks=2,
            is_headless=True,
            on_relaunch_headed=on_relaunch,
        )
        self.assertTrue(res)
        self.assertEqual(len(relaunch_called), 0, "自动清障成功时绝不应触发有头重启")
        self.assertTrue(mock_click.called)

    @patch("core.cloudflare_turnstile.bring_browser_window_to_front")
    @patch("core.cloudflare_turnstile.try_click_turnstile_cross_origin")
    def test_auto_clear_fail_triggers_headed_and_user_solves(self, mock_click, mock_front):
        """场景二：自动清障失败，切换有头窗口弹出，人工在超时前解决验证。"""
        driver = MockDriver(title="Just a moment...", is_challenge=True, is_headless=True)
        headed_driver = MockDriver(title="Just a moment...", is_challenge=True, is_headless=False)
        relaunch_called = []

        def on_relaunch():
            relaunch_called.append(True)
            return headed_driver

        mock_click.return_value = False  # 自动清障点击未生效

        call_count = 0

        def fake_is_challenge(d):
            nonlocal call_count
            call_count += 1
            # 前几次（检查+2次清障尝试+切换）处于盾状态，切换到有头且人工解决后放行
            if d is headed_driver and call_count > 6:
                return False
            return True

        with patch("core.cloudflare_turnstile.is_cloudflare_challenge", side_effect=fake_is_challenge):
            res = solve_or_wait_cloudflare_turnstile(
                driver,
                timeout=10,
                max_auto_clicks=2,
                is_headless=True,
                on_relaunch_headed=on_relaunch,
            )

        self.assertTrue(res)
        self.assertEqual(len(relaunch_called), 1, "自动清障失败后应调用1次有头重启")
        self.assertTrue(mock_front.called, "应调用窗口置顶函数")

    @patch("core.cloudflare_turnstile.bring_browser_window_to_front")
    @patch("core.cloudflare_turnstile.try_click_turnstile_cross_origin")
    def test_auto_clear_fail_headed_timeout(self, mock_click, mock_front):
        """场景三：自动清障失败，切换有头窗口弹出，人工在规定时间内未解决（超时），返回 False。"""
        driver = MockDriver(title="Just a moment...", is_challenge=True, is_headless=True)
        headed_driver = MockDriver(title="Just a moment...", is_challenge=True, is_headless=False)
        relaunch_called = []

        def on_relaunch():
            relaunch_called.append(True)
            return headed_driver

        mock_click.return_value = False

        # 始终处于盾状态，超时设定为非常短 (0.1秒)
        with patch("core.cloudflare_turnstile.is_cloudflare_challenge", return_value=True):
            res = solve_or_wait_cloudflare_turnstile(
                driver,
                timeout=0.1,
                max_auto_clicks=1,
                is_headless=True,
                on_relaunch_headed=on_relaunch,
            )

        self.assertFalse(res)
        self.assertEqual(len(relaunch_called), 1)

    @patch("core.cloakbrowser_registration._safe_get")
    @patch("core.cloakbrowser_registration.build_cloak_driver")
    @patch("core.email_provider.release_email")
    def test_run_cloak_registration_handles_verification_timeout(self, mock_release, mock_build, mock_safe_get):
        """场景四：完整注册函数在遇到人机验证超时时，释放邮箱、关闭浏览器并返回 skipped=True。"""
        from core.cloakbrowser_registration import _run_cloak_registration_impl

        mock_driver = MockDriver(title="Just a moment...", is_challenge=True, is_headless=True)
        mock_build.return_value = (mock_driver, MagicMock(profile_id="p1", raw={}))

        with patch("core.cloudflare_turnstile.solve_or_wait_cloudflare_turnstile", return_value=False):
            result = _run_cloak_registration_impl(
                email="test_timeout@example.com",
                name="Test User",
                birthday="2000-01-01",
            )

        self.assertFalse(result.get("success"))
        self.assertTrue(result.get("skipped"))
        self.assertIn("人机验证等待超时", result.get("error", ""))
        self.assertTrue(mock_driver.quit_called, "超时后应正常关闭浏览器")
        # 验证邮箱被标记为 failed 且有相应 note，从而触发资金解冻
        mock_release.assert_called_with("test_timeout@example.com", status="failed", note="人机验证超时未解决")


if __name__ == "__main__":
    unittest.main()
