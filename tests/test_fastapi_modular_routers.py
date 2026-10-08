# -*- coding: utf-8 -*-
"""测试 FastAPI 模块化 Routers 挂载与响应。"""
from fastapi.testclient import TestClient
import pytest
from webui.server import app

client = TestClient(app)

def test_dashboard_overview_endpoint():
    """验证 /api/dashboard/overview 路由。"""
    resp = client.get("/api/dashboard/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "vault" in data
    assert "runtime" in data

def test_vault_accounts_endpoint():
    """验证 /api/vault/accounts 路由。"""
    resp = client.get("/api/vault/accounts?page=1&page_size=5")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "items" in data

def test_vault_metrics_endpoint():
    """验证 /api/vault/metrics 路由。"""
    resp = client.get("/api/vault/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "total_accounts" in data

def test_network_country_stats_endpoint():
    """验证 /api/proxy/country-stats 路由。"""
    resp = client.get("/api/proxy/country-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data

def test_mailpool_list_endpoint():
    """验证 /api/mailpool/list 路由。"""
    resp = client.get("/api/mailpool/list?page=1&page_size=5")
    assert resp.status_code == 200
    data = resp.json()
    assert "ok" in data
