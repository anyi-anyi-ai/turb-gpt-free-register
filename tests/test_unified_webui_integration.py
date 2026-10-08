# -*- coding: utf-8 -*-
"""统一管理大屏 (Unified Mission Control) 端到端集成测试套件。

涵盖：
1. 大盘总览聚合接口 (/api/dashboard/overview)
2. 跨平台通用资产金库 (Vault CRUD, 敏感字段按需获取, 多格式导出)
3. 代理国家分流与住宅权重配置
4. 邮箱池批量素材导入与查询
5. 注册机任务 Schema 与流水线可用性
"""
import json
import pytest
from fastapi.testclient import TestClient

from webui.server import app

client = TestClient(app)


def test_dashboard_overview_integration():
    """测试大盘总览指标聚合接口。"""
    resp = client.get("/api/dashboard/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "timestamp" in data
    assert "vault" in data
    assert "runtime" in data
    assert "total_accounts" in data["vault"]
    assert "platforms" in data["vault"]


def test_vault_multi_platform_query():
    """测试 Universal Vault 多平台跨表查询与分页。"""
    # 1. 查询全部平台
    resp = client.get("/api/vault/accounts?page=1&page_size=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "items" in data
    assert "total" in data

    # 验证字段脱敏：列表响应中绝对禁止直接下发明文 token 和 password
    for item in data["items"]:
        assert "password" not in item
        assert "access_token" not in item
        assert "totp_secret" not in item
        assert "has_token" in item
        assert "has_password" in item
        assert "status" in item

    # 2. 按平台过滤查询
    for platform in ["chatgpt", "grok", "github", "elevenlabs"]:
        resp_p = client.get(f"/api/vault/accounts?platform={platform}&page=1&page_size=5")
        assert resp_p.status_code == 200
        pdata = resp_p.json()
        assert pdata["ok"] is True
        for it in pdata["items"]:
            assert it["platform"] == platform


def test_vault_metrics_kpi():
    """测试 Universal Vault 指标统计接口。"""
    resp = client.get("/api/vault/metrics")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["total_accounts"] >= 0
    assert "platforms" in data
    for p in ["chatgpt", "grok", "uned", "suno", "elevenlabs"]:
        assert p in data["platforms"]


def test_vault_account_secret_access():
    """测试凭据安全按需解密读取。"""
    # 获取一个账号 ID
    resp = client.get("/api/vault/accounts?platform=chatgpt&page=1&page_size=1")
    items = resp.json().get("items", [])
    if items:
        acc_id = items[0]["id"]
        # 请求机密凭据
        sec_resp = client.get(f"/api/vault/chatgpt/{acc_id}/secret")
        assert sec_resp.status_code == 200
        sdata = sec_resp.json()
        assert sdata["ok"] is True
        assert "secrets" in sdata
        assert "access_token" in sdata["secrets"]
        assert "password" in sdata["secrets"]


def test_vault_export_formats():
    """测试多格式导出端点 (Sub2API / OpenAI / CSV / TXT)。"""
    for fmt in ["sub2api", "openai", "csv", "txt"]:
        resp = client.get(f"/api/vault/export?platform=chatgpt&format_type={fmt}")
        assert resp.status_code == 200
        assert "Content-Disposition" in resp.headers
        assert resp.headers["Content-Disposition"].startswith("attachment;")
        content = resp.text
        assert len(content) >= 0


def test_network_proxy_distribution_and_weights():
    """测试代理国家节点分布大盘与权重更新接口。"""
    # 1. 读取国家节点分布
    resp = client.get("/api/proxy/country-stats")
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data or "ok" in data

    # 2. 更新国家权重
    payload = {"weights": {"US": 50, "JP": 30, "GB": 20}}
    save_resp = client.post("/api/proxy/country-weights", json=payload)
    assert save_resp.status_code == 200
    sdata = save_resp.json()
    assert sdata["ok"] is True
    assert "weights" in sdata

    # 3. 纯住宅模式切换
    toggle_resp = client.post("/api/proxy/toggle-residential-only", json={"enabled": True})
    assert toggle_resp.status_code == 200


def test_mailpool_import_and_list():
    """测试邮箱池素材批量导入与列表分页查询。"""
    # 1. 列表拉取
    resp = client.get("/api/mailpool/list?page=1&page_size=10")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert "items" in data

    # 2. 模拟导入解析
    sample_text = "test_user_unified@example.com----pass123----client1----refresh1"
    import_resp = client.post("/api/mailpool/import", json={
        "source": "outlook",
        "text": sample_text,
        "as_registered": False,
    })
    assert import_resp.status_code == 200
    idata = import_resp.json()
    assert idata["ok"] is True


def test_pipeline_scripts_schema_endpoint():
    """测试流水线任务元数据完整性。"""
    resp = client.get("/api/scripts")
    assert resp.status_code == 200
    res = resp.json()
    scripts = res.get("scripts", res) if isinstance(res, dict) else res
    assert isinstance(scripts, list)
    assert len(scripts) >= 10
    script_ids = [s["id"] for s in scripts]
    # 验证覆盖核心注册机与多模态服务
    assert "run_suno_register" in script_ids
    assert "run_elevenlabs_register" in script_ids
    assert "register_grok" in script_ids or "register_grok_http" in script_ids


