# -*- coding: utf-8 -*-
import time
import unittest
from unittest.mock import MagicMock

from core.white_screen_guard import (
    WhiteScreenError,
    is_white_screen,
    wait_with_white_screen_guard,
)


class MockDriver:
    """Mock WebDriver / Playwright Driver supporting execute_script, refresh, and page.reload."""

    def __init__(self, js_return_value: bool = False, current_url: str = "https://chatgpt.com/auth/login"):
        self.js_return_value = js_return_value
        self.current_url = current_url
        self.refresh_called = 0
        self.page = MagicMock()
        self.page.url = current_url

    def execute_script(self, script: str, *args):
        if callable(self.js_return_value):
            return self.js_return_value(self)
        return self.js_return_value

    def refresh(self, timeout_ms: int | None = None):
        self.refresh_called += 1


class TestWhiteScreenGuard(unittest.TestCase):

    def test_is_white_screen_returns_false_for_normal_page(self):
        driver = MockDriver(js_return_value=False)
        self.assertFalse(is_white_screen(driver))

    def test_is_white_screen_returns_true_for_blank_page(self):
        driver = MockDriver(js_return_value=True)
        self.assertTrue(is_white_screen(driver))

    def test_wait_immediate_success(self):
        driver = MockDriver(js_return_value=False)
        result = wait_with_white_screen_guard(
            driver,
            check_success_fn=lambda d: "FoundTarget",
            timeout=5.0,
            blank_threshold=1.0,
            max_refreshes=2,
            poll_interval=0.05,
        )
        self.assertEqual(result, "FoundTarget")
        self.assertEqual(driver.refresh_called, 0)

    def test_wait_recovers_after_one_refresh(self):
        # 初始白屏，在被刷新一次后页面恢复并返回目标
        driver = MockDriver()

        def dynamic_js(d):
            # 刷新前为白屏 (True)，刷新后恢复正常 (False)
            return d.refresh_called == 0

        driver.js_return_value = dynamic_js

        def check_target(d):
            if d.refresh_called >= 1:
                return "RecoveredTarget"
            return None

        result = wait_with_white_screen_guard(
            driver,
            check_success_fn=check_target,
            timeout=5.0,
            blank_threshold=0.2,  # 0.2s 快速触发刷新
            max_refreshes=2,
            poll_interval=0.05,
        )
        self.assertEqual(result, "RecoveredTarget")
        self.assertEqual(driver.refresh_called, 1)

    def test_wait_raises_white_screen_error_when_refreshes_exhausted(self):
        # 始终为白屏，刷新 2 次后仍无内容，抛出 WhiteScreenError
        driver = MockDriver(js_return_value=True)

        with self.assertRaises(WhiteScreenError):
            wait_with_white_screen_guard(
                driver,
                check_success_fn=lambda d: None,
                timeout=5.0,
                blank_threshold=0.1,  # 快速测试
                max_refreshes=2,
                poll_interval=0.05,
            )
        self.assertEqual(driver.refresh_called, 2)

    def test_wait_raises_timeout_error_if_not_blank_but_target_missing(self):
        # 页面有内容（不是白屏），但等待目标超时，应抛出普通的 TimeoutError
        driver = MockDriver(js_return_value=False)

        with self.assertRaises(TimeoutError):
            wait_with_white_screen_guard(
                driver,
                check_success_fn=lambda d: None,
                timeout=0.3,
                blank_threshold=1.0,
                max_refreshes=2,
                poll_interval=0.05,
            )
        self.assertEqual(driver.refresh_called, 0)

    def test_handle_blank_page_ignores_cloudflare(self):
        from core.roxy_registration import _handle_blank_page
        driver = MockDriver(js_return_value=True)
        # URL 带 CF 标志，直接忽略不当作白屏
        state = {"url": "https://chatgpt.com/auth/login?__cf_chl_tk=123", "text": "", "inputs": [], "buttons": []}
        result = _handle_blank_page(driver, state, blank_start_time=time.time() - 10, blank_threshold=4.0)
        self.assertIsNone(result)
        self.assertEqual(driver.refresh_called, 0)

    def test_handle_blank_page_triggers_refresh_and_eventual_error(self):
        from core.roxy_registration import _handle_blank_page
        driver = MockDriver(js_return_value=True)
        state = {"url": "https://chatgpt.com/auth/login", "text": "", "inputs": [], "buttons": []}

        # 第 1 次超时 -> 刷新 (第 1 次)
        start_t = time.time() - 5.0
        new_t = _handle_blank_page(driver, state, blank_start_time=start_t, blank_threshold=4.0, max_refreshes=2)
        self.assertEqual(driver.refresh_called, 1)
        self.assertIsNotNone(new_t)

        # 第 2 次超时 -> 刷新 (第 2 次)
        start_t = time.time() - 5.0
        new_t = _handle_blank_page(driver, state, blank_start_time=start_t, blank_threshold=4.0, max_refreshes=2)
        self.assertEqual(driver.refresh_called, 2)
        self.assertIsNotNone(new_t)

        # 第 3 次超时 -> 达到 max_refreshes=2 上限，抛出 WhiteScreenError
        start_t = time.time() - 5.0
        with self.assertRaises(WhiteScreenError):
            _handle_blank_page(driver, state, blank_start_time=start_t, blank_threshold=4.0, max_refreshes=2)


if __name__ == "__main__":
    unittest.main()
