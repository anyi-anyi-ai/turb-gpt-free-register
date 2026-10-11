# -*- coding: utf-8 -*-
"""测试 CloakBrowser 账号专属指纹种子派生、解析与隔离机制。"""
import unittest
from unittest.mock import MagicMock, patch

from core.fingerprint import (
    derive_account_fingerprint_seed,
    resolve_account_fingerprint_seed,
)


class CloakFingerprintIsolationTests(unittest.TestCase):
    def test_derive_seed_deterministic_and_case_insensitive(self):
        """测试相同邮箱派生的种子恒定，且大小写和空格不敏感。"""
        seed1 = derive_account_fingerprint_seed("TestUser123@Example.Com")
        seed2 = derive_account_fingerprint_seed("  testuser123@example.com  ")
        self.assertEqual(seed1, seed2)
        self.assertEqual(len(seed1), 16)
        # 必须是 16 进制字符串
        int(seed1, 16)

    def test_derive_seed_different_for_different_emails(self):
        """测试不同邮箱派生出互不相同的种子。"""
        s1 = derive_account_fingerprint_seed("alice@example.com")
        s2 = derive_account_fingerprint_seed("bob@example.com")
        self.assertNotEqual(s1, s2)

    def test_derive_seed_empty_email_produces_random_seed(self):
        """测试未传邮箱时生成有效随机种子。"""
        r1 = derive_account_fingerprint_seed("")
        r2 = derive_account_fingerprint_seed(None)
        self.assertEqual(len(r1), 16)
        self.assertEqual(len(r2), 16)
        self.assertNotEqual(r1, r2)

    def test_resolve_seed_from_direct_field(self):
        """测试从直接字段读取种子。"""
        acc = {"fingerprint_seed": "1122334455667788", "email": "any@example.com"}
        self.assertEqual(resolve_account_fingerprint_seed(acc), "1122334455667788")

    def test_resolve_seed_from_extra_json(self):
        """测试从 extra_json 读取种子。"""
        acc = {
            "email": "any@example.com",
            "extra_json": '{"fingerprint_seed": "aabbccddeeff0011"}',
        }
        self.assertEqual(resolve_account_fingerprint_seed(acc), "aabbccddeeff0011")

    def test_resolve_seed_from_cloak_meta_in_extra(self):
        """测试从 extra.cloakbrowser 读取种子。"""
        acc = {
            "email": "any@example.com",
            "extra": {"cloakbrowser": {"fingerprint_seed": "9988776655443322"}},
        }
        self.assertEqual(resolve_account_fingerprint_seed(acc), "9988776655443322")

    def test_resolve_seed_from_profile_id(self):
        """测试从 profile_id 形如 cloak_xxxx 中提取种子。"""
        acc = {
            "profile_id": "cloak_1234567890abcdef",
            "email": "different@example.com",
        }
        self.assertEqual(resolve_account_fingerprint_seed(acc), "1234567890abcdef")

    def test_resolve_seed_fallback_to_email_derivation(self):
        """测试老账号未保存种子时，自动按邮箱确定性推导。"""
        email = "legacy_user@example.com"
        acc = {"email": email}
        expected = derive_account_fingerprint_seed(email)
        self.assertEqual(resolve_account_fingerprint_seed(acc), expected)

    @patch("cloakbrowser.launch")
    def test_build_cloak_driver_seed_precedence(self, mock_launch):
        """测试 build_cloak_driver 的指纹种子优先级控制。"""
        from core.cloakbrowser_driver import build_cloak_driver
        import config.cloakbrowser as cfg

        mock_context = MagicMock()
        mock_page = MagicMock()
        mock_browser = MagicMock()
        mock_browser.new_context.return_value = mock_context
        mock_context.new_page.return_value = mock_page
        mock_launch.return_value = mock_browser

        # 1. 当全局 CLOAK_FINGERPRINT_SEED 有值时，最高优先级生效
        orig_global = getattr(cfg, "CLOAK_FINGERPRINT_SEED", "")
        try:
            cfg.CLOAK_FINGERPRINT_SEED = "global_override_seed"
            driver, opened = build_cloak_driver(fingerprint_seed="local_seed")
            self.assertEqual(opened.profile_id, "cloak_global_override_seed")
            self.assertEqual(opened.raw["fingerprint_seed"], "global_override_seed")

            # 检查传递给 launch 的 args 是否含有 --fingerprint=global_override_seed
            launch_call = mock_launch.call_args
            launch_args = launch_call.kwargs.get("args") or []
            self.assertIn("--fingerprint=global_override_seed", launch_args)
        finally:
            cfg.CLOAK_FINGERPRINT_SEED = orig_global

        # 2. 全局为空时，传入的 fingerprint_seed 生效
        driver, opened = build_cloak_driver(fingerprint_seed="account_seed_99")
        self.assertEqual(opened.profile_id, "cloak_account_seed_99")
        self.assertEqual(opened.raw["fingerprint_seed"], "account_seed_99")
        launch_call = mock_launch.call_args
        launch_args = launch_call.kwargs.get("args") or []
        self.assertIn("--fingerprint=account_seed_99", launch_args)

        # 3. 传入 account_email 时自动派生
        test_mail = "auto_derive@example.com"
        expected_seed = derive_account_fingerprint_seed(test_mail)
        driver, opened = build_cloak_driver(account_email=test_mail)
        self.assertEqual(opened.profile_id, f"cloak_{expected_seed}")
        self.assertEqual(opened.raw["fingerprint_seed"], expected_seed)
        launch_call = mock_launch.call_args
        launch_args = launch_call.kwargs.get("args") or []
        self.assertIn(f"--fingerprint={expected_seed}", launch_args)


if __name__ == "__main__":
    unittest.main()
