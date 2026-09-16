# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch
from core.cloudflare_turnstile import (
    is_cloudflare_challenge,
    bring_browser_window_to_front,
    solve_or_wait_cloudflare_turnstile,
)


class MockDriver:
    def __init__(self, url="", title="", cf_dom=False):
        self.current_url = url
        self.title = title
        self.cf_dom = cf_dom
        self.page = MagicMock()
        self.page.title.return_value = title
        self.page.frames = []
        self._registration_log_prefix = "[Test]"

    def execute_script(self, script, *args):
        return self.cf_dom


class TestCloudflareTurnstile(unittest.TestCase):
    def test_detects_cf_by_url(self):
        driver = MockDriver(url="https://chatgpt.com/auth/login?__cf_chl_rt_tk=12345")
        self.assertTrue(is_cloudflare_challenge(driver))

    def test_detects_cf_by_title(self):
        driver = MockDriver(title="Just a moment...")
        self.assertTrue(is_cloudflare_challenge(driver))

    def test_detects_cf_by_dom(self):
        driver = MockDriver(cf_dom=True)
        self.assertTrue(is_cloudflare_challenge(driver))

    def test_not_cf_when_normal_page(self):
        driver = MockDriver(url="https://chatgpt.com/auth/login", title="ChatGPT", cf_dom=False)
        self.assertFalse(is_cloudflare_challenge(driver))

    def test_solve_or_wait_returns_true_immediately_when_no_challenge(self):
        driver = MockDriver(url="https://chatgpt.com/auth/login", title="ChatGPT", cf_dom=False)
        result = solve_or_wait_cloudflare_turnstile(driver, timeout=10)
        self.assertTrue(result)

    def test_solve_or_wait_respects_stop_callback(self):
        driver = MockDriver(title="Just a moment...", cf_dom=True)
        stop_called = []

        def stop_cb():
            stop_called.append(True)
            raise RuntimeError("用户手动停止")

        with self.assertRaises(RuntimeError):
            solve_or_wait_cloudflare_turnstile(driver, timeout=10, check_stop_cb=stop_cb)
        self.assertTrue(len(stop_called) > 0)

    @patch("core.cloudflare_turnstile.try_click_turnstile_cross_origin")
    def test_solve_or_wait_auto_click_success(self, mock_click):
        driver = MockDriver(title="Just a moment...", cf_dom=True)
        # First attempt clicked, then on next check challenge is gone
        def mock_click_impl(d):
            d.title = "ChatGPT"
            d.cf_dom = False
            d.page.title.return_value = "ChatGPT"
            return True

        mock_click.side_effect = mock_click_impl
        result = solve_or_wait_cloudflare_turnstile(driver, timeout=10, max_auto_clicks=2)
        self.assertTrue(result)


if __name__ == "__main__":
    unittest.main()
