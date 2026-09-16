# -*- coding: utf-8 -*-
import unittest
from unittest.mock import patch

from core import outlook_client


class OutlookClientContextTests(unittest.TestCase):
    def setUp(self):
        outlook_client._CONTEXT_CACHE.clear()

    @patch("core.db.get_account_by_email")
    @patch("core.db.get_outlook_by_email", return_value=None)
    def test_restores_context_from_registered_account_after_pool_missing(
        self,
        get_outlook_by_email,
        get_account_by_email,
    ):
        get_account_by_email.return_value = {
            "email": "Registered@outlook.test",
            "email_source": "outlook",
            "password": "mail-password",
            "client_id": "client-id",
            "refresh_token": "refresh-token",
            "recovery_email": "recovery@example.test",
            "recovery_code": "recovery-code",
        }

        account = outlook_client.get_account_context("  registered@OUTLOOK.test ")

        self.assertIsNotNone(account)
        self.assertEqual(account.email, "Registered@outlook.test")
        self.assertEqual(account.password, "mail-password")
        self.assertEqual(account.client_id, "client-id")
        self.assertEqual(account.refresh_token, "refresh-token")
        self.assertEqual(account.recovery_email, "recovery@example.test")
        self.assertEqual(account.recovery_code, "recovery-code")
        get_outlook_by_email.assert_called_once_with("  registered@OUTLOOK.test ")
        get_account_by_email.assert_called_once_with("  registered@OUTLOOK.test ")

    @patch("core.db.get_account_by_email")
    @patch("core.db.get_outlook_by_email")
    def test_registered_account_fills_incomplete_pool_context(
        self,
        get_outlook_by_email,
        get_account_by_email,
    ):
        get_outlook_by_email.return_value = {
            "email": "registered@outlook.test",
            "password": "pool-password",
            "client_id": "",
            "refresh_token": "pool-refresh",
        }
        get_account_by_email.return_value = {
            "email": "registered@outlook.test",
            "email_source": "outlook",
            "password": "saved-password",
            "client_id": "saved-client",
            "refresh_token": "saved-refresh",
        }

        account = outlook_client.get_account_context("registered@outlook.test")

        self.assertIsNotNone(account)
        self.assertEqual(account.password, "pool-password")
        self.assertEqual(account.client_id, "saved-client")
        self.assertEqual(account.refresh_token, "pool-refresh")

    @patch("core.db.get_account_by_email", return_value={
        "email": "registered@outlook.test",
        "email_source": "remail",
        "password": "saved-password",
        "client_id": "saved-client",
        "refresh_token": "saved-refresh",
    })
    @patch("core.db.get_outlook_by_email", return_value={
        "email": "registered@outlook.test",
        "password": "pool-password",
        "client_id": "pool-client",
        "refresh_token": "pool-refresh",
    })
    def test_non_outlook_registered_source_is_not_read_from_outlook_pool(
        self,
        get_outlook_by_email,
        get_account_by_email,
    ):
        self.assertIsNone(outlook_client.get_account_context("registered@outlook.test"))

    @patch("core.db.get_account_by_email", return_value=None)
    @patch("core.db.get_outlook_by_email", return_value={
        "email": "Pool@outlook.test",
        "password": "pool-password",
        "client_id": "pool-client",
        "refresh_token": "pool-refresh",
    })
    def test_context_cache_is_case_insensitive(self, get_outlook_by_email, get_account_by_email):
        first = outlook_client.get_account_context("pool@outlook.test")
        second = outlook_client.get_account_context(" POOL@OUTLOOK.TEST ")

        self.assertIs(first, second)
        get_outlook_by_email.assert_called_once_with("pool@outlook.test")
        get_account_by_email.assert_called_once_with("pool@outlook.test")

    def test_auto_swap_reversed_tokens(self):
        long_rt = "M.C508_SN1.0.U.MsaArtifacts." + "a" * 400
        uuid_cid = "9e5f94bc-e8a4-4e73-b8be-63364c29d753"

        # Reverse passed: client_id has long_rt, refresh_token has uuid_cid
        acc = outlook_client.OutlookAccount(
            email="test@outlook.com",
            password="pwd",
            client_id=long_rt,
            refresh_token=uuid_cid,
        )
        self.assertEqual(acc.client_id, uuid_cid)
        self.assertEqual(acc.refresh_token, long_rt)

        # Correctly passed: keeps as is
        acc2 = outlook_client.OutlookAccount(
            email="test@outlook.com",
            password="pwd",
            client_id=uuid_cid,
            refresh_token=long_rt,
        )
        self.assertEqual(acc2.client_id, uuid_cid)
        self.assertEqual(acc2.refresh_token, long_rt)


if __name__ == "__main__":
    unittest.main()
