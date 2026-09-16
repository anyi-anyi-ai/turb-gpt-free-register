# -*- coding: utf-8 -*-
"""自动化注册优化回归测试。"""
import unittest
from unittest.mock import MagicMock, patch


class TestRegistrationOptimizations(unittest.TestCase):
    def test_outlook_client_global_variable_and_imap_skip(self):
        """测试 _REMOTE_IMAP_UNSUPPORTED 正常访问与 Graph 命中跳过 IMAP。"""
        from core import outlook_client

        # 验证全局变量存在且不会引发 UnboundLocalError
        self.assertFalse(outlook_client._REMOTE_IMAP_UNSUPPORTED)

        # 模拟 fetch_latest_otp 中的循环逻辑
        account = MagicMock()
        session = MagicMock()

        call_count = {"graph": 0, "imap": 0}

        def mock_fetch_via(sess, proto, acc):
            call_count[proto] += 1
            if proto == "graph":
                return [{"subject": "Your OpenAI code is 123456", "date": "2026-09-16T00:00:00Z"}]
            return []

        with patch.object(outlook_client, "_fetch_via", side_effect=mock_fetch_via):
            with patch.object(outlook_client, "get_account_context", return_value=account):
                with patch.object(outlook_client, "_http_session", return_value=session):
                    otp = outlook_client.fetch_latest_otp(
                        "test@outlook.com",
                        max_wait=2,
                        poll_interval=1,
                        settle_seconds=0,
                    )
                    self.assertEqual(otp, "123456")
                    # 验证 Graph 成功后 IMAP 被短路跳过，未执行请求
                    self.assertEqual(call_count["graph"], 1)
                    self.assertEqual(call_count["imap"], 0, "Graph 成功后应短路跳过 IMAP")

    def test_cloak_selenium_driver_has_title(self):
        """测试 CloakSeleniumDriver 拥有与 Selenium 一致的 title 属性。"""
        from core.cloakbrowser_driver import CloakSeleniumDriver

        mock_page = MagicMock()
        mock_page.title.return_value = "しばらくお待ちください..."
        mock_page.url = "https://chatgpt.com/auth/login"

        driver = CloakSeleniumDriver(browser=MagicMock(), context=MagicMock(), page=mock_page)
        self.assertEqual(driver.title, "しばらくお待ちください...")
        self.assertEqual(driver.current_url, "https://chatgpt.com/auth/login")

    def test_build_cloak_driver_releases_quota_on_launch_failure(self):
        """测试 build_cloak_driver 在浏览器启动失败时自动释放 IP 配额。"""
        from core import cloakbrowser_driver

        with patch("config.proxy.pick_proxy", return_value="http://JP.sess_99:pwd@127.0.0.1:2260"):
            with patch("core.ip_quota_manager.ip_quota_manager.release_in_flight") as mock_release:
                with patch("cloakbrowser.launch", side_effect=RuntimeError("Browser launch failed")):
                    with self.assertRaises(RuntimeError):
                        cloakbrowser_driver.build_cloak_driver(proxy=None, country="JP")
                    mock_release.assert_called_once_with("http://JP.sess_99:pwd@127.0.0.1:2260")


if __name__ == "__main__":
    unittest.main()
