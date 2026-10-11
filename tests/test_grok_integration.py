# -*- coding: utf-8 -*-
"""Grok 模块集成与单元测试。"""
import os
import tempfile
import pytest

from core import db
from core.grok.quality_checker import GrokQualityChecker, GrokQualityResult
from core.grok.flow import _ALREADY_REGISTERED_PATTERNS, _DOMAIN_REJECTION_PATTERNS


def test_grok_db_crud():
    test_email = "test_grok_unit@example.com"
    account_id = db.insert_grok_account(
        email=test_email,
        password="TestPassword123!",
        sso_token="sso_token_sample_abc",
        status="healthy",
        bot_flag_source=0,
        bot_flag_details="",
        probe_tps=45.2,
        probe_reasoning_tokens=120,
    )
    assert account_id > 0

    acc = db.get_grok_account_by_email(test_email)
    assert acc is not None
    assert acc["email"] == test_email
    assert acc["status"] == "healthy"
    assert acc["probe_tps"] == 45.2
    assert acc["probe_reasoning_tokens"] == 120

    # 导出测试
    exported_txt = db.export_grok_accounts_text(format_type="txt")
    assert test_email in exported_txt
    assert "sso=sso_token_sample_abc" in exported_txt

    # 查询列表
    res = db.query_grok_accounts(search=test_email)
    assert res["total"] >= 1
    assert any(item["email"] == test_email for item in res["items"])

    # 删除测试
    deleted = db.delete_grok_accounts(emails=[test_email])
    assert deleted >= 1
    assert db.get_grok_account_by_email(test_email) is None


def test_grok_quality_checker_bot_flag_parsing():
    # 模拟正常账号 HTML
    clean_html = """
    <html>
      <script id="__NEXT_DATA__">
        {"initialData": {"user": {"id": "usr_123", "botFlagSource": 0, "botFlagDetails": ""}}}
      </script>
    </html>
    """
    user = GrokQualityChecker._extract_initial_user(clean_html)
    assert user is not None
    assert user["id"] == "usr_123"
    src, det = GrokQualityChecker._extract_bot_flags(user, clean_html)
    assert src == 0

    # 模拟降智/被标记账号 HTML
    flagged_html = """
    <html>
      <script>
        window.__data = {"initialData": {"user": {"id": "usr_bad", "botFlagSource": 1, "botFlagDetails": "policy=deny, event=$registration"}}};
      </script>
    </html>
    """
    user_flagged = GrokQualityChecker._extract_initial_user(flagged_html)
    assert user_flagged is not None
    src_f, det_f = GrokQualityChecker._extract_bot_flags(user_flagged, flagged_html)
    assert src_f == 1
    assert "policy=deny" in det_f


def test_grok_flow_regex_patterns():
    # 测试邮箱已注册模式
    sample_text_1 = "An account with this email already exists."
    assert any(p.search(sample_text_1) for p in _ALREADY_REGISTERED_PATTERNS)

    sample_text_cn = "已存在与此邮箱地址关联的账号，请直接登录。"
    assert any(p.search(sample_text_cn) for p in _ALREADY_REGISTERED_PATTERNS)

    # 测试域名受限模式
    sample_rej = "This email address is not available or unsupported."
    assert any(p.search(sample_rej) for p in _DOMAIN_REJECTION_PATTERNS)


def test_grok_webui_api():
    from webui.app import create_app
    app = create_app()
    app.config["TESTING"] = True
    client = app.test_client()

    with client.session_transaction() as sess:
        sess["webui_auth_ok"] = True

    # 1. 状态查询
    res = client.get("/api/grok/status")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "is_running" in data

    # 2. 账号分页查询
    res = client.get("/api/grok/accounts")
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "items" in data

    # 3. 导出接口
    res = client.get("/api/grok/accounts/export?format=txt")
    assert res.status_code == 200

    # 4. 轻量快速测活接口 (mock 测活函数与账号列表)
    import core.grok.sso_checker as sso_mod
    import core.db as db_mod
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(
        db_mod,
        "query_grok_accounts",
        lambda **kwargs: {"items": [{"id": 1, "email": "test@grok.com", "sso_token": "tok"}], "total": 1},
    )
    from core.grok.sso_checker import SsoCheckResult
    monkeypatch.setattr(
        sso_mod,
        "batch_check_grok_liveness",
        lambda accs, concurrency=5, explicit_proxy=None: [
            SsoCheckResult(ok=True, status="healthy", email=a.get("email", ""), account_id=a.get("id", 0))
            for a in accs
        ],
    )
    res = client.post("/api/grok/accounts/liveness-check", json={"ids": []})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert "counts" in data

    # 5. gptgrok2api 推送接口
    import core.gptgrok2api_sync as sync_mod
    monkeypatch.setattr(
        sync_mod,
        "push_grok_accounts_to_gptgrok2api",
        lambda accs: {"ok": True, "count": len(accs)},
    )
    res = client.post("/api/grok/accounts/push-gptgrok2api", json={"ids": []})
    assert res.status_code in (200, 400)


def test_grok_proxy_distribution_migration():
    """验证 Grok 注册页面已成功集成代理国家节点监控与分流权重面板及相关逻辑。"""
    # 1. 验证 index.html 模板中包含了 Grok 代理节点监控与权重分流面板的所有 DOM 元素
    template_path = os.path.join(os.path.dirname(__file__), "..", "webui", "templates", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        html = f.read()

    assert 'id="grokProxyDistributionPanel"' in html
    assert 'id="btnGrokRefreshProxyStats"' in html
    assert 'id="btnGrokToggleCleanNodes"' in html
    assert 'id="btnGrokAutoWeightByResidential"' in html
    assert 'id="btnGrokEqualWeight"' in html
    assert 'id="btnGrokSaveProxyWeights"' in html
    assert 'id="grokProxyTotalWeightLabel"' in html
    assert 'id="grokProxyDistBar"' in html
    assert 'id="grokProxyCardsGrid"' in html
    assert 'id="btnGrokToggleCleanNodes"' in html

    # 2. 验证前端 JS 逻辑中包含双面板同步与事件绑定
    assert "btnGrokRefreshProxyStats" in html
    assert "btnGrokToggleCleanNodes" in html
    assert "btnGrokSaveProxyWeights" in html
    assert "loadGrokRegisterTab" in html

    # 3. 验证后端 Grok 注册入口自动遵循国家分流代理逻辑
    from unittest.mock import patch, MagicMock
    from core.grok.registration_service import register_single_grok_account

    mock_driver = MagicMock()
    mock_driver.page = MagicMock()
    mock_driver._proxy_url = "http://JP.sess_99:token@127.0.0.1:2260"

    with patch("core.grok.registration_service.build_cloak_driver", return_value=(mock_driver, MagicMock())) as mock_build, \
         patch("config.proxy.pick_proxy", return_value="http://JP.sess_99:token@127.0.0.1:2260"), \
         patch("socket.socket") as mock_sock:

        mock_sock_inst = MagicMock()
        mock_sock_inst.connect_ex.return_value = 0
        mock_sock.return_value.__enter__.return_value = mock_sock_inst

        # 留空 proxy 时，自动选用国家分流挑选的代理
        res = register_single_grok_account(
            proxy="",
            country="JP",
            stop_checker=lambda: True,
            run_quality_probe=False,
            enable_device_oauth=False,
        )
        assert mock_build.called
        call_kwargs = mock_build.call_args[1]
        assert "JP.sess_99" in (call_kwargs.get("proxy") or "")
        assert res.get("error") == "任务被手动终止"


