# -*- coding: utf-8 -*-
import string
import pytest

from core.browser_use_registration import _generate_password
from core.grok.registration_service import generate_strong_password
from register_outlook_standalone import generate_email_password


def test_browser_use_password_generator():
    for _ in range(20):
        pwd = _generate_password(16)
        assert len(pwd) == 16
        assert any(c in string.ascii_uppercase for c in pwd)
        assert any(c in string.ascii_lowercase for c in pwd)
        assert any(c in string.digits for c in pwd)
        assert any(c in "!@#$%^&*" for c in pwd)


def test_grok_password_generator():
    for _ in range(20):
        pwd = generate_strong_password(14)
        assert len(pwd) == 14
        assert any(c in string.ascii_uppercase for c in pwd)
        assert any(c in string.ascii_lowercase for c in pwd)
        assert any(c in string.digits for c in pwd)
        assert any(c in "@#$%^&*!" for c in pwd)


def test_outlook_password_generator():
    passwords = set()
    for _ in range(20):
        email, pwd, prefix = generate_email_password()
        assert pwd.startswith("Aa1!")
        assert len(pwd) == 16  # "Aa1!" (4) + 12 chars
        passwords.add(pwd)
    # 20 次随机生成的密码应当各不相同
    assert len(passwords) == 20
