# -*- coding: utf-8 -*-
"""
动态住宅代理模块单元测试
"""
import pytest
from unittest.mock import patch, MagicMock
from core.dynamic_residential_proxy import DynamicResidentialProxyManager


def test_manager_singleton():
    m1 = DynamicResidentialProxyManager()
    m2 = DynamicResidentialProxyManager()
    assert m1 is m2


def test_account_success_and_rotation_threshold():
    mgr = DynamicResidentialProxyManager()
    mgr._accounts_on_current_ip = 0
    mgr._current_proxy_host = "1.2.3.4"
    mgr._current_proxy_port = 8888
    
    # 模拟账号成功 1
    cnt1 = mgr.record_account_success("acc1@test.com")
    assert cnt1 == 1
    assert mgr._accounts_on_current_ip == 1

    # 模拟账号成功 2 (达到上限)
    cnt2 = mgr.record_account_success("acc2@test.com")
    assert cnt2 == 2
    assert mgr._accounts_on_current_ip == 2


def test_failure_invalidation():
    mgr = DynamicResidentialProxyManager()
    mgr._current_proxy_host = "1.2.3.4"
    mgr._current_proxy_port = 8888
    mgr._accounts_on_current_ip = 1

    mgr.record_proxy_failure("Network timeout")
    assert mgr._current_proxy_host is None
    assert mgr._current_proxy_port is None
    assert mgr._accounts_on_current_ip == 0


def test_config_defaults():
    mgr = DynamicResidentialProxyManager()
    assert mgr.local_port == 28888
    assert len(mgr.regions) > 0
    assert "US" in mgr.regions
    assert mgr.max_accounts_per_ip in (1, 2)
