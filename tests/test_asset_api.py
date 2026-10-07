# -*- coding: utf-8 -*-
"""Unit tests for the mounted /api/assets/* endpoints in WebUI."""
import json
import os
import unittest
from unittest.mock import patch

from webui.app import create_app


class TestAssetApi(unittest.TestCase):
    def setUp(self):
        self.app = create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.api_key = "test_asset_secret"
        self.headers = {"X-API-Key": self.api_key}

    @patch("common.asset_store.summary")
    def test_asset_summary_endpoint(self, mock_summary):
        mock_summary.return_value = {
            "emails": {"total": 10, "unclaimed": 5, "claimed": 5},
            "cookies": {"chatgpt": {"total": 3, "unclaimed": 2, "claimed": 1}},
        }
        with patch.dict(os.environ, {"REG_FACTORY_ASSET_API_KEY": self.api_key}):
            res = self.client.get("/api/assets/summary", headers=self.headers)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("ok"))
            self.assertIn("emails", data.get("data", {}))
            self.assertEqual(data["data"]["emails"]["total"], 10)

    @patch("common.asset_store.get_email")
    def test_asset_emails_claim_endpoint(self, mock_get_email):
        mock_get_email.return_value = {
            "ok": True,
            "email": "test@outlook.com",
            "password": "pwd",
            "token": "rt",
            "client_id": "cid",
            "index": 1,
        }
        with patch.dict(os.environ, {"REG_FACTORY_ASSET_API_KEY": self.api_key}):
            res = self.client.get("/api/assets/emails", headers=self.headers)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertTrue(data.get("ok"))
            self.assertEqual(data.get("email"), "test@outlook.com")

    @patch("common.asset_store.get_platform_asset")
    def test_asset_cookies_claim_endpoint(self, mock_get_platform_asset):
        mock_get_platform_asset.return_value = {
            "content": {"access_token": "token123"},
            "index": 1,
        }
        with patch.dict(os.environ, {"REG_FACTORY_ASSET_API_KEY": self.api_key}):
            res = self.client.get("/api/assets/cookies/chatgpt?format=cookies", headers=self.headers)
            self.assertEqual(res.status_code, 200)
            data = res.get_json()
            self.assertEqual(data.get("access_token"), "token123")

    @patch.dict(os.environ, {"REG_FACTORY_ASSET_API_KEY": "secret123"})
    def test_asset_auth_enforcement(self):
        # 1. No key -> 401
        res = self.client.get("/api/assets/summary")
        self.assertEqual(res.status_code, 401)

        # 2. Correct X-API-Key -> 200
        with patch("common.asset_store.summary", return_value={"test": True}):
            res_auth = self.client.get(
                "/api/assets/summary",
                headers={"X-API-Key": "secret123"},
            )
            self.assertEqual(res_auth.status_code, 200)

        # 3. Correct Bearer token -> 200
        with patch("common.asset_store.summary", return_value={"test": True}):
            res_bearer = self.client.get(
                "/api/assets/summary",
                headers={"Authorization": "Bearer secret123"},
            )
            self.assertEqual(res_bearer.status_code, 200)

    @patch.dict(os.environ, {"REG_FACTORY_ASSET_API_KEY": ""})
    def test_asset_localhost_fallback(self):
        with patch("common.asset_store.summary", return_value={"test": True}):
            res = self.client.get("/api/assets/summary")
            self.assertEqual(res.status_code, 200)

    def test_heal_by_email_auth_enforcement(self):
        # 1. No token -> 401
        res = self.client.post("/api/accounts/heal-by-email", json={"email": "a@b.com"})
        self.assertEqual(res.status_code, 401)

        # 2. With valid X-Heal-Token -> not 401
        with patch.dict(os.environ, {"HEAL_TOKEN": "my_heal_token_123"}):
            res_token = self.client.post(
                "/api/accounts/heal-by-email",
                json={"email": "nonexistent@test.com"},
                headers={"X-Heal-Token": "my_heal_token_123"},
            )
            self.assertIn(res_token.status_code, (404, 200, 202))


if __name__ == "__main__":
    unittest.main()
