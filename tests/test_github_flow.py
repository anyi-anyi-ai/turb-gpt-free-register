# -*- coding: utf-8 -*-
"""Unit tests for GitHub registration flow and Arkose dual-track solver."""
import unittest
from unittest.mock import MagicMock, patch

from core.github.flow import try_auto_solve_arkose, wait_for_arkose_and_takeover


class TestGithubFlow(unittest.TestCase):
    def test_try_auto_solve_arkose_no_keys(self):
        page = MagicMock()
        with patch.dict("os.environ", {}, clear=True):
            res = try_auto_solve_arkose(page, timeout=1)
            self.assertFalse(res)

    def test_try_auto_solve_arkose_already_verified(self):
        page = MagicMock()
        page.url = "https://github.com/account_verifications"
        with patch.dict("os.environ", {"YESCAPTCHA_API_KEY": "dummy_key"}):
            res = try_auto_solve_arkose(page, timeout=1)
            self.assertTrue(res)

    def test_wait_for_arkose_takeover_when_auto_passes(self):
        page = MagicMock()
        with patch("core.github.flow.try_auto_solve_arkose", return_value=True):
            res = wait_for_arkose_and_takeover(page, max_wait=5)
            self.assertTrue(res)

    def test_wait_for_arkose_takeover_when_url_navigates(self):
        page = MagicMock()
        page.url = "https://github.com/account_verifications"
        with patch("core.github.flow.try_auto_solve_arkose", return_value=False):
            res = wait_for_arkose_and_takeover(page, max_wait=5)
            self.assertTrue(res)


if __name__ == "__main__":
    unittest.main()
