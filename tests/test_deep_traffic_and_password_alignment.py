# -*- coding: utf-8 -*-
"""深度省流量策略、二轮问答分流与 OpenAI 密码对齐服务单元测试。"""
import unittest
from unittest.mock import MagicMock, patch
from pathlib import Path

from config import warming as warming_cfg
from config import browser as browser_cfg
from core.account_warming import AccountWarmingService, warm_single_account
from core.openai_password_service import (
    generate_secure_password,
    set_openai_account_password,
)


class TestDeepTrafficAndDialogueSelectivity(unittest.TestCase):
    """验证深度省流与问答交互分流配置。"""

    def test_warming_dialogue_config_defaults(self):
        """验证养号问答默认配置：默认关闭二轮问答（极省流量模式）。"""
        self.assertFalse(warming_cfg.WARMING_ENABLE_DIALOGUE)
        self.assertEqual(warming_cfg.WARMING_DIALOGUE_TURNS, 2)

    def test_browser_shared_cache_config(self):
        """验证跨账号静态响应体磁盘缓存配置。"""
        self.assertTrue(browser_cfg.BROWSER_SHARED_CACHE_ENABLED)
        self.assertEqual(browser_cfg.BROWSER_SHARED_CACHE_ROOT, "data/browser_cache")

    def test_account_warming_service_fast_recovery_skips_dialogue(self):
        """验证自愈模式下，未开启问答时（enable_dialogue=False），只捕获凭证并秒级退出，不执行问答。"""
        fake_acc = {
            "id": 88881,
            "email": "test_fast_recovery@example.com",
            "country": "US",
            "storage_state": {"cookies": [{"name": "session-token", "value": "test"}]},
        }
        service = AccountWarmingService(fake_acc, headless=True, enable_dialogue=False)
        self.assertFalse(service.enable_dialogue)

        # 模拟浏览器与会话
        service.page = MagicMock()
        service.driver = MagicMock()
        service.driver.context.storage_state.return_value = {"cookies": [{"name": "new_session", "value": "fresh"}]}
        service.latest_access_token = "fresh_at_token"

        with patch.object(service, "_init_browser"), \
             patch.object(service, "_guard_page"), \
             patch.object(service, "_check_session_alive", return_value=(True, None)), \
             patch.object(service, "_action_natural_chat") as mock_chat, \
             patch("core.db.update_account_warming_result"):
            res = service.run()

            # 验证未执行问答动作，直接极速完成
            mock_chat.assert_not_called()
            self.assertTrue(res.get("ok"))
            self.assertEqual(res.get("action"), "fast_recovery")
            self.assertEqual(res.get("access_token"), "fresh_at_token")
            self.assertIn("storage_state", res)

    def test_account_warming_service_enable_dialogue(self):
        """验证当显式开启 enable_dialogue=True 时，正常进入拟人问答流程。"""
        fake_acc = {
            "id": 88882,
            "email": "test_warm_dialogue@example.com",
            "country": "US",
            "storage_state": {"cookies": [{"name": "session-token", "value": "test"}]},
        }
        service = AccountWarmingService(fake_acc, headless=True, enable_dialogue=True, dialogue_turns=2)
        self.assertTrue(service.enable_dialogue)
        self.assertEqual(service.dialogue_turns, 2)

        service.page = MagicMock()
        service.driver = MagicMock()
        service.driver.context.storage_state.return_value = {"cookies": []}

        with patch.object(service, "_init_browser"), \
             patch.object(service, "_guard_page"), \
             patch.object(service, "_check_session_alive", return_value=(True, None)), \
             patch.object(service, "_action_natural_chat", return_value={"action": "natural_chat", "prompt": "Hi", "turns": 2}) as mock_chat, \
             patch("random.choices", return_value=["chat"]), \
             patch("time.sleep"), \
             patch("core.db.update_account_warming_result"):
            res = service.run()

            # 验证确实执行了拟人问答
            mock_chat.assert_called_once()
            self.assertTrue(res.get("ok"))
            self.assertEqual(res.get("action"), "natural_chat")


class TestOpenAIPasswordService(unittest.TestCase):
    """验证 OpenAI 独立密码对齐与生成机制。"""

    def test_generate_secure_password(self):
        """验证 16 位强密码满足多样性要求。"""
        pwd = generate_secure_password(16)
        self.assertEqual(len(pwd), 16)
        # 必须包含大写、小写、数字、符号
        has_upper = any(c.isupper() for c in pwd)
        has_lower = any(c.islower() for c in pwd)
        has_digit = any(c.isdigit() for c in pwd)
        has_symbol = any(c in "!@#$%^&*?_-+=" for c in pwd)
        self.assertTrue(has_upper and has_lower and has_digit and has_symbol)

    def test_set_openai_account_password_orchestration(self):
        """验证密码设置服务的端到端编排。"""
        mock_driver = MagicMock()
        mock_driver.page = MagicMock()

        with patch("core.openai_password_service.request_password_reset_email", return_value=True) as mock_req, \
             patch("core.openai_password_service.wait_for_password_reset_link", return_value="https://auth.openai.com/u/reset?ticket=123") as mock_poll, \
             patch("core.openai_password_service.submit_new_password_form", return_value=True) as mock_submit, \
             patch("core.db.update_account_registration_password") as mock_db_update:

            result_pwd = set_openai_account_password(
                mock_driver,
                "user@example.com",
                new_password="TestPassword123!",
                account_id=101,
            )

            self.assertEqual(result_pwd, "TestPassword123!")
            mock_req.assert_called_once()
            mock_poll.assert_called_once()
            mock_submit.assert_called_once()
            mock_db_update.assert_called_once_with(101, "TestPassword123!")


if __name__ == "__main__":
    unittest.main()
