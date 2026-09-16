# -*- coding: utf-8 -*-
import pytest
from unittest.mock import patch, MagicMock

from core.gptgrok2api_sync import (
    map_country_to_group_id,
    GROUP_ID_ASIA,
    GROUP_ID_EUROPE,
    GROUP_ID_AMERICAS,
    on_account_completed,
    push_accounts_to_gptgrok2api,
    BATCH_SYNC_SIZE,
)


def test_map_country_to_group_id():
    # 亚洲
    assert map_country_to_group_id("JP") == GROUP_ID_ASIA
    assert map_country_to_group_id("SG") == GROUP_ID_ASIA
    assert map_country_to_group_id(None, "http://JP.sess_1:pass@127.0.0.1:2260") == GROUP_ID_ASIA
    assert map_country_to_group_id(None, "http://SG.sess_2:pass@127.0.0.1:2260") == GROUP_ID_ASIA

    # 欧洲
    assert map_country_to_group_id("GB") == GROUP_ID_EUROPE
    assert map_country_to_group_id("DE") == GROUP_ID_EUROPE
    assert map_country_to_group_id(None, "http://DE.sess_1:pass@127.0.0.1:2260") == GROUP_ID_EUROPE
    assert map_country_to_group_id(None, "http://GB.sess_1:pass@127.0.0.1:2260") == GROUP_ID_EUROPE

    # 美洲
    assert map_country_to_group_id("US") == GROUP_ID_AMERICAS
    assert map_country_to_group_id("CA") == GROUP_ID_AMERICAS
    assert map_country_to_group_id(None, "http://US.sess_1:pass@127.0.0.1:2260") == GROUP_ID_AMERICAS

    # 缺省 fallback 为美洲
    assert map_country_to_group_id("") == GROUP_ID_AMERICAS


def test_batch_threshold_logic():
    # 测试当待推送账号未达到批次单位（例如 1 < 3）时，等待不推送
    with patch("core.gptgrok2api_sync.get_unpushed_accounts", return_value=[{"id": 1, "email": "test@a.com"}]):
        res = on_account_completed("test@a.com", force_check=False)
        assert res["ok"] is True
        assert res["pushed"] is False
        assert res["pending"] == 1
        assert res["batch_size"] == 3

    # 测试当达到 3 个账号时，自动触发推送
    mock_push = MagicMock(return_value={"ok": True, "added": 3, "skipped": 0})
    with patch("core.gptgrok2api_sync.get_unpushed_accounts", return_value=[
        {"id": 1, "email": "a@test.com"},
        {"id": 2, "email": "b@test.com"},
        {"id": 3, "email": "c@test.com"},
    ]), patch("core.gptgrok2api_sync.push_accounts_to_gptgrok2api", mock_push):
        res = on_account_completed("c@test.com", force_check=False)
        mock_push.assert_called_once()
        assert res["ok"] is True
        assert res["added"] == 3
