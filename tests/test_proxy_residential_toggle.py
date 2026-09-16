# -*- coding: utf-8 -*-
"""
Tests for Proxy Residential-Only Routing Toggle.
"""
import pytest
from unittest.mock import patch

from core import db, proxy_stats
from webui.app import create_app


@pytest.fixture(autouse=True)
def reset_storage():
    """Ensure proxy_residential_only is reset before and after test."""
    db.set_proxy_residential_only(False)
    yield
    db.set_proxy_residential_only(False)


def test_db_residential_only_toggle():
    assert db.is_proxy_residential_only() is False
    db.set_proxy_residential_only(True)
    assert db.is_proxy_residential_only() is True
    db.set_proxy_residential_only(False)
    assert db.is_proxy_residential_only() is False


def test_proxy_stats_is_residential_only():
    assert proxy_stats.is_residential_only() is False
    db.set_proxy_residential_only(True)
    assert proxy_stats.is_residential_only() is True


def test_apply_residential_routing_with_mock():
    mock_nodes = {
        "items": [
            {
                "region": "us",
                "enabled": True,
                "has_outbound": True,
                "circuit_open_since": None,
                "egress_ip": "1.1.1.1",
                "tags": [{"tag": "US-Res-Node-1"}]
            },
            {
                "region": "us",
                "enabled": True,
                "has_outbound": True,
                "circuit_open_since": None,
                "egress_ip": "2.2.2.2",
                "tags": [{"tag": "US-Datacenter-Node-2"}]
            }
        ]
    }
    mock_platforms = {
        "items": [
            {"id": "pid-us", "name": "US", "regex_filters": [".*"]}
        ]
    }

    patched_calls = []

    def fake_resin_req(path, method="GET", body=None, timeout=8.0):
        if path.startswith("/api/v1/nodes"):
            return mock_nodes
        if path == "/api/v1/platforms":
            return mock_platforms
        if method == "PATCH" and path.startswith("/api/v1/platforms/"):
            patched_calls.append({"path": path, "body": body})
            return {"routable_node_count": 1}
        return {}

    with patch("core.proxy_stats._resin_request", side_effect=fake_resin_req), \
         patch("core.proxy_stats.is_ip_residential", side_effect=lambda ip: ip == "1.1.1.1"), \
         patch("core.proxy_stats.preload_ip_intelligence"):
        res = proxy_stats.apply_residential_routing(True)
        assert res["ok"] is True
        assert res["residential_only"] is True
        assert db.is_proxy_residential_only() is True
        assert len(patched_calls) == 1
        assert "US\\-Res\\-Node\\-1" in patched_calls[0]["body"]["regex_filters"][0]

        patched_calls.clear()
        res_off = proxy_stats.apply_residential_routing(False)
        assert res_off["ok"] is True
        assert res_off["residential_only"] is False
        assert db.is_proxy_residential_only() is False
        assert len(patched_calls) == 1
        assert patched_calls[0]["body"]["regex_filters"] == [".*"]


def test_api_toggle_residential_only():
    app = create_app(auth_code="test-auth")
    client = app.test_client()
    client.environ_base["HTTP_X_AUTH_CODE"] = "test-auth"

    with patch("core.proxy_stats.apply_residential_routing") as mock_apply, \
         patch("core.proxy_stats.is_residential_only") as mock_is_res:
        mock_apply.return_value = {"ok": True, "residential_only": True, "items": []}
        mock_is_res.return_value = False

        resp = client.post("/api/proxy/toggle-residential-only", json={"enabled": True})
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert data["residential_only"] is True
        mock_apply.assert_called_with(True)
