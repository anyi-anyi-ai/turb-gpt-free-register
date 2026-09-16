# -*- coding: utf-8 -*-
import unittest
from unittest.mock import Mock, patch

from core import mailnest_client


class MailNestClientTests(unittest.TestCase):
    def setUp(self):
        mailnest_client._CONTEXT_CACHE.clear()

    def test_pick_account_requires_api_key(self):
        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "", create=True):
            with self.assertRaisesRegex(mailnest_client.MailNestClientError, "MailNest API Key"):
                mailnest_client.pick_account()

    @patch("core.mailnest_client.requests.request")
    def test_pick_account_buys_temporary_mailbox(self, request):
        response = Mock(status_code=200)
        response.json.return_value = {
            "code": "00000",
            "data": [{"id": "id-123", "email": "fresh@mailnest.test", "sale_mode": "temporary"}],
        }
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True), patch.object(
            mailnest_client._email_cfg, "MAIL_NEST_MODE", "temporary", create=True
        ), patch.object(mailnest_client._email_cfg, "MAIL_NEST_PROJECT_CODE", "chatgpt001", create=True):
            account = mailnest_client.pick_account()

        self.assertEqual(account.email, "fresh@mailnest.test")
        self.assertEqual(account.mailbox_id, "id-123")
        self.assertEqual(account.mode, "temporary")
        self.assertIs(mailnest_client.get_account_context("fresh@mailnest.test"), account)
        request.assert_called_once()
        kwargs = request.call_args.kwargs
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer key-123")
        self.assertEqual(kwargs["json"], {"project_code": "chatgpt001", "count": 1})

    @patch("core.mailnest_client.requests.request")
    def test_pick_account_buys_exclusive_mailbox(self, request):
        response = Mock(status_code=200)
        response.json.return_value = {
            "code": "00000",
            "data": [{"id": "ex-456", "email": "exclusive@mailnest.test", "sale_mode": "exclusive"}],
        }
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True), patch.object(
            mailnest_client._email_cfg, "MAIL_NEST_MODE", "exclusive", create=True
        ):
            account = mailnest_client.pick_account()

        self.assertEqual(account.email, "exclusive@mailnest.test")
        self.assertEqual(account.mailbox_id, "ex-456")
        self.assertEqual(account.mode, "exclusive")
        request.assert_called_once()
        kwargs = request.call_args.kwargs
        self.assertIn("/api/v1/email/exclusive/buy", request.call_args.args[1])
        self.assertEqual(kwargs["json"], {"count": 1})

    @patch("core.mailnest_client.requests.request")
    def test_pick_account_business_error_handling(self, request):
        response = Mock(status_code=200)
        response.json.return_value = {"code": "D0001", "msg": "余额不足", "data": None}
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True):
            with self.assertRaisesRegex(mailnest_client.MailNestClientError, "余额不足"):
                mailnest_client.pick_account()

    @patch("core.mailnest_client.requests.request")
    def test_release_account_calls_api_when_unconsumed(self, request):
        response = Mock(status_code=200)
        response.json.return_value = {"code": "00000", "data": None}
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True):
            mailnest_client._CONTEXT_CACHE["test@mailnest.test"] = mailnest_client.MailNestAccount(
                email="test@mailnest.test"
            )
            mailnest_client.release_account("test@mailnest.test", status="available")

        self.assertNotIn("test@mailnest.test", mailnest_client._CONTEXT_CACHE)
        request.assert_called_once()
        self.assertIn("/api/v1/email/release", request.call_args.args[1])
        self.assertEqual(request.call_args.kwargs["json"], {"email": "test@mailnest.test"})

    @patch("core.mailnest_client.requests.request")
    def test_release_account_skips_api_when_used(self, request):
        mailnest_client._CONTEXT_CACHE["used@mailnest.test"] = mailnest_client.MailNestAccount(
            email="used@mailnest.test"
        )
        mailnest_client.release_account("used@mailnest.test", status="used")
        self.assertNotIn("used@mailnest.test", mailnest_client._CONTEXT_CACHE)
        request.assert_not_called()

    @patch("core.mailnest_client.time.sleep")
    @patch("core.mailnest_client.requests.request")
    def test_fetch_latest_otp_reads_code_match(self, request, sleep):
        response = Mock(status_code=200)
        response.json.return_value = {
            "code": "00000",
            "data": [
                {
                    "code_match": "654321",
                    "subject": "Your OpenAI verification code",
                    "from_email": "noreply@tm.openai.com",
                    "received_at": "2026-06-10T12:03:00+08:00",
                }
            ],
        }
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True):
            code = mailnest_client.fetch_latest_otp(
                "fresh@mailnest.test",
                after_ts=1700000000,
                max_wait=1,
                poll_interval=1,
                settle_seconds=0,
            )

        self.assertEqual(code, "654321")

    @patch("core.mailnest_client.time.sleep")
    @patch("core.mailnest_client.requests.request")
    def test_fetch_latest_otp_extracts_from_html_body(self, request, sleep):
        response = Mock(status_code=200)
        response.json.return_value = {
            "code": "00000",
            "data": [
                {
                    "subject": "Your ChatGPT security code",
                    "from_name": "OpenAI",
                    "from_email": "noreply@tm.openai.com",
                    "body": "<p>Your code is 889900.</p>",
                    "body_type": "html",
                    "code_match": "",
                    "received_at": "2026-06-10T12:03:00+08:00",
                }
            ],
        }
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True):
            code = mailnest_client.fetch_latest_otp(
                "fresh@mailnest.test",
                after_ts=1700000000,
                max_wait=1,
                poll_interval=1,
                settle_seconds=0,
            )

        self.assertEqual(code, "889900")

    @patch("core.mailnest_client.requests.request")
    def test_get_balance_and_product_info(self, request):
        response = Mock(status_code=200)
        response.json.return_value = {
            "code": "00000",
            "data": {"balance": "10.00", "available_balance": "9.50"},
        }
        request.return_value = response

        with patch.object(mailnest_client._email_cfg, "MAIL_NEST_API_KEY", "key-123", create=True):
            balance = mailnest_client.get_balance()
        self.assertEqual(balance["balance"], "10.00")


if __name__ == "__main__":
    unittest.main()
