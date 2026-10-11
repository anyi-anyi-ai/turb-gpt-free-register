# -*- coding: utf-8 -*-
import unittest
from unittest.mock import MagicMock, patch
from core.roxy_registration import (
    PageStageAdvanced,
    AlreadyRegisteredError,
    UserAlreadyExistsError,
    _detect_current_auth_stage,
    _is_email_verification_page,
    _is_user_already_exists_page,
    _wait_for_email_input,
    _submit_email_and_wait_next,
)


class MockDriver:
    def __init__(self, url="", current_url="", title="", script_returns=None):
        self.url = url or current_url
        self.current_url = current_url or url
        self.title = title
        self._script_returns = script_returns or {}
        self._registration_log_prefix = "[TestDriver]"

    def execute_script(self, script, *args):
        for key, val in self._script_returns.items():
            if key in script:
                if callable(val):
                    return val(script, *args)
                return val
        return None


class TestPageStageDetection(unittest.TestCase):
    def test_detect_otp_by_url(self):
        driver = MockDriver(current_url="https://auth.openai.com/email-verification")
        self.assertTrue(_is_email_verification_page(driver))
        self.assertEqual(_detect_current_auth_stage(driver), "otp")

    def test_detect_otp_by_discrete_pin_boxes(self):
        # 6 discrete input boxes
        otp_state = {
            "url": "https://auth.openai.com/u/mfa",
            "title": "OpenAI",
            "inputs": [
                {"type": "text", "inputmode": "numeric", "maxlength": "1", "id": "digit-0"},
                {"type": "text", "inputmode": "numeric", "maxlength": "1", "id": "digit-1"},
                {"type": "text", "inputmode": "numeric", "maxlength": "1", "id": "digit-2"},
                {"type": "text", "inputmode": "numeric", "maxlength": "1", "id": "digit-3"},
                {"type": "text", "inputmode": "numeric", "maxlength": "1", "id": "digit-4"},
                {"type": "text", "inputmode": "numeric", "maxlength": "1", "id": "digit-5"},
            ],
            "buttons": [{"text": "Resend code"}],
            "text": ""
        }
        driver = MockDriver(
            current_url="https://auth.openai.com/u/mfa",
            script_returns={"react-aria-FieldError": otp_state}
        )
        self.assertTrue(_is_email_verification_page(driver))
        self.assertEqual(_detect_current_auth_stage(driver), "otp")

    def test_detect_otp_by_multilingual_japanese_text(self):
        otp_state = {
            "url": "https://auth.openai.com/flow",
            "title": "OpenAI",
            "inputs": [{"type": "text", "id": "code"}],
            "buttons": [],
            "text": "受信トレイを確認してください。確認コードを入力してください。"
        }
        driver = MockDriver(
            current_url="https://auth.openai.com/flow",
            script_returns={"react-aria-FieldError": otp_state}
        )
        self.assertTrue(_is_email_verification_page(driver))
        self.assertEqual(_detect_current_auth_stage(driver), "otp")

    def test_detect_cloudflare_challenge_japanese(self):
        cf_dom = {
            "isChallenge": True,
            "reason": "body_challenge_text"
        }
        driver = MockDriver(
            current_url="https://auth.openai.com/api/accounts/authorize?client_id=app_123",
            script_returns={"isChallenge": cf_dom}
        )
        self.assertEqual(_detect_current_auth_stage(driver), "cloudflare_challenge")

    def test_wait_for_email_input_raises_page_stage_advanced(self):
        # When page is on email-verification, wait_for_email_input must raise PageStageAdvanced
        driver = MockDriver(current_url="https://auth.openai.com/email-verification")
        with self.assertRaises(PageStageAdvanced) as ctx:
            _wait_for_email_input(driver, timeout=2)
        self.assertEqual(ctx.exception.stage, "otp")

    @patch("core.roxy_registration._wait_email_submit_next_state")
    @patch("core.roxy_registration._submit_email_step")
    @patch("core.roxy_registration._email_input_value_state")
    @patch("core.roxy_registration._type_email_address")
    def test_submit_email_and_wait_next_catches_advanced_stage(
        self, mock_type, mock_val_state, mock_submit, mock_wait
    ):
        mock_type.side_effect = PageStageAdvanced("otp")
        driver = MockDriver(current_url="https://auth.openai.com/email-verification")
        
        result = _submit_email_and_wait_next(driver, email="test@example.com", attempts=2)
        self.assertEqual(result, "otp")

    def test_submit_email_and_wait_next_returns_immediately_if_already_on_otp(self):
        driver = MockDriver(current_url="https://auth.openai.com/email-verification")
        result = _submit_email_and_wait_next(driver, email="test@example.com", attempts=2)
        self.assertEqual(result, "otp")

    def test_type_otp_refreshes_when_no_inputs(self):
        from core.roxy_registration import _type_otp
        driver = MockDriver(current_url="https://auth.openai.com/email-verification")
        driver.refreshed = False
        refresh_calls = []

        def mock_refresh():
            driver.refreshed = True
            refresh_calls.append(True)
            # After refresh, simulate OTP inputs appearing
            driver._script_returns["react-aria-FieldError"] = {
                "url": "https://auth.openai.com/email-verification",
                "title": "Verification",
                "inputs": [{"type": "text", "name": "code", "id": "code", "autocomplete": "one-time-code", "value": ""}],
                "buttons": [],
                "text": "Check your inbox"
            }

        driver.refresh = mock_refresh
        driver.find_elements = MagicMock(return_value=[])

        # Initially empty OTP state
        driver._script_returns["react-aria-FieldError"] = {
            "url": "https://auth.openai.com/email-verification",
            "title": "",
            "inputs": [],
            "buttons": [],
            "text": ""
        }

        with patch("time.sleep", return_value=None):
            clock = [100.0]
            def fake_time():
                val = clock[0]
                clock[0] += 1.5
                return val
            with patch("time.time", side_effect=fake_time):
                # Now mock find_elements to succeed after refresh
                def mock_find_elements(by, selector):
                    if driver.refreshed and "one-time-code" in selector:
                        mock_el = MagicMock()
                        mock_el.is_displayed.return_value = True
                        mock_el.size = {"width": 100, "height": 30}
                        mock_el.get_attribute.return_value = "123456"
                        return [mock_el]
                    return []
                driver.find_elements = mock_find_elements
                _type_otp(driver, "123456", timeout=10)

        self.assertTrue(driver.refreshed)
        self.assertEqual(len(refresh_calls), 1)

    def test_detect_user_already_exists_stage(self):
        # 模拟页面包含 error_code: user_already_exists
        driver = MockDriver(
            current_url="https://auth.openai.com/u/signup/identifier",
            script_returns={
                "user_already_exists": True,
            }
        )
        self.assertTrue(_is_user_already_exists_page(driver))
        self.assertEqual(_detect_current_auth_stage(driver), "user_already_exists")

    def test_submit_email_and_wait_next_raises_user_already_exists(self):
        driver = MockDriver(
            current_url="https://auth.openai.com/u/signup/identifier",
            script_returns={
                "user_already_exists": True,
            }
        )
        with self.assertRaises(UserAlreadyExistsError):
            _submit_email_and_wait_next(driver, email="exists@test.com", attempts=1)

    def test_detect_logged_in_not_triggered_by_guest_homepage(self):
        # 访客无 token 且无 profile-button 时，访问 chatgpt.com/ 绝不能误判为 logged_in
        driver = MockDriver(current_url="https://chatgpt.com/")
        stage = _detect_current_auth_stage(driver)
        self.assertNotEqual(stage, "logged_in")

        # 当页面挂载了 profile-button 等真实登录特有元素时，才确认为 logged_in
        driver_with_user = MockDriver(
            current_url="https://chatgpt.com/",
            script_returns={"profile-button": True}
        )
        self.assertEqual(_detect_current_auth_stage(driver_with_user), "logged_in")

    def test_otp_wait_does_not_falsely_accept_on_verification_page(self):
        from core.auth_flow.otp_step import _wait_after_email_otp_submit
        # 模拟提交 OTP 后超时依然在 email-verification，绝不能返回 accepted
        driver = MockDriver(
            current_url="https://auth.openai.com/email-verification",
            script_returns={
                "react-aria-FieldError": {
                    "url": "https://auth.openai.com/email-verification",
                    "inputs": [{"name": "code", "value": "158941"}],
                    "errors": [],
                }
            }
        )
        outcome = _wait_after_email_otp_submit(driver, timeout=1)
        self.assertEqual(outcome, "invalid")

    def test_password_step_skips_when_on_email_verification(self):
        from core.auth_flow.password_step import _fill_password_page_if_present
        # 当页面已处于验证码页时，_fill_password_page_if_present 必须直接返回 None
        driver = MockDriver(current_url="https://auth.openai.com/email-verification")
        res = _fill_password_page_if_present(driver, "test@test.com", timeout=1)
        self.assertIsNone(res)


if __name__ == "__main__":
    unittest.main()
