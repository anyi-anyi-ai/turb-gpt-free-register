from core.otp_utils import mask_otp, mask_secret


def test_mask_otp():
    assert mask_otp(None) == ""
    assert mask_otp("") == ""
    assert mask_otp("12") == "***"
    assert mask_otp("123456") == "1****6"
    assert mask_otp("449759") == "4****9"
    assert mask_otp("987654321") == "9*******1"


def test_mask_pwd():
    assert mask_secret(None) == "***"
    assert mask_secret("") == "***"
    assert mask_secret("123") == "***"
    assert mask_secret("1234") == "***"
    assert mask_secret("Aa1!password123") == "Aa***23"
    assert "Aa1!password123" not in mask_secret("Aa1!password123")
