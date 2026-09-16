# -*- coding: utf-8 -*-
"""
IP Quota Manager and Session Rotation Unit Tests
"""
import pytest
from unittest.mock import patch
from core.ip_quota_manager import IPQuotaManager, _normalize_proxy_url
import config.proxy as proxy_cfg


@pytest.fixture
def clean_quota_mgr():
    mgr = IPQuotaManager()
    mgr._initialized = True
    mgr._state = {
        "proxy_counts": {},
        "country_sessions": {},
    }
    return mgr


def test_is_resin_proxy(clean_quota_mgr):
    assert clean_quota_mgr.is_resin_proxy("http://US:resin_proxy_123@127.0.0.1:2260") is True
    assert clean_quota_mgr.is_resin_proxy("http://127.0.0.1:2260") is True
    assert clean_quota_mgr.is_resin_proxy("http://user:pass@1.2.3.4:8080") is False
    assert clean_quota_mgr.is_resin_proxy("") is False


def test_build_resin_session_url(clean_quota_mgr):
    base = "http://US:resin_token@127.0.0.1:2260"
    url = clean_quota_mgr._build_resin_session_url(base, "US", 1)
    assert "US.sess_1" in url
    assert "resin_token" in url
    assert "127.0.0.1:2260" in url


def test_resin_session_advances_after_quota_reached(clean_quota_mgr):
    with patch.object(clean_quota_mgr, "_save_to_storage"):
        with patch.object(proxy_cfg, "MAX_ACCOUNTS_PER_IP", 2):
            with patch.dict(proxy_cfg.PROXY_COUNTRY_POOLS, {"US": ["http://US:token@127.0.0.1:2260"]}):
                # 第一次获取：sess_1
                p1 = clean_quota_mgr.get_registration_proxy("US")
                assert "US.sess_1" in p1

                # 记录第 1 个成功账号
                cnt1 = clean_quota_mgr.record_account_success(p1, "US", "user1@example.com")
                assert cnt1 == 1

                # 第二次获取：依然是 sess_1 (未达上限 2)
                p2 = clean_quota_mgr.get_registration_proxy("US")
                assert "US.sess_1" in p2

                # 记录第 2 个成功账号 (达到上限 2)
                cnt2 = clean_quota_mgr.record_account_success(p2, "US", "user2@example.com")
                assert cnt2 == 2

                # 第三次获取：自动步进至 sess_2
                p3 = clean_quota_mgr.get_registration_proxy("US")
                assert "US.sess_2" in p3

                # 记录第 3 个账号
                cnt3 = clean_quota_mgr.record_account_success(p3, "US", "user3@example.com")
                assert cnt3 == 1


def test_static_proxy_pool_advances(clean_quota_mgr):
    with patch.object(clean_quota_mgr, "_save_to_storage"):
        with patch.object(proxy_cfg, "MAX_ACCOUNTS_PER_IP", 2):
            with patch.dict(proxy_cfg.PROXY_COUNTRY_POOLS, {"SG": ["http://node1:8080", "http://node2:8080"]}):
                # 第一次获取 node1
                p1 = clean_quota_mgr.get_registration_proxy("SG")
                assert p1 == "http://node1:8080"

                clean_quota_mgr.record_account_success(p1, "SG", "a@test.com")
                clean_quota_mgr.record_account_success(p1, "SG", "b@test.com")

                # node1 用满后，自动轮换至 node2
                p2 = clean_quota_mgr.get_registration_proxy("SG")
                assert p2 == "http://node2:8080"


def test_strict_same_country_policy():
    account = {
        "email": "test@example.com",
        "country": "JP",
        "proxy_used": "http://JP.sess_1:token@127.0.0.1:2260",
    }
    # check_alive=False 时复用原 IP
    p = proxy_cfg.pick_proxy_for_account(account, check_alive=False)
    assert p == "http://JP.sess_1:token@127.0.0.1:2260"

    # check_alive=True 时在同国代理池中轮换
    with patch.dict(proxy_cfg.PROXY_COUNTRY_POOLS, {"JP": ["http://JP:token@127.0.0.1:2260"]}):
        new_p = proxy_cfg.pick_proxy_for_account(account, check_alive=True)
        assert "JP" in new_p

    # 不存在的国家严格阻断
    bad_account = {"email": "bad@example.com", "country": "ZZ"}
    with pytest.raises(RuntimeError) as exc_info:
        proxy_cfg.pick_proxy_for_account(bad_account)
    assert "严格同国策略已阻止跨国请求" in str(exc_info.value)
