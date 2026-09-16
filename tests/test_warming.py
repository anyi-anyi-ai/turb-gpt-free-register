# -*- coding: utf-8 -*-
"""测试 ChatGPT 账号拟人养号引擎、调度器与 WebUI 接口。"""
import json
import pytest
from unittest.mock import MagicMock, patch

from config import warming as warming_cfg
from core.account_warming import BezierMouse, HumanTyper, load_random_prompt
from core import db, warming_scheduler
from webui.app import create_app


def test_warming_config_and_prompts():
    """测试养号配置与语料库加载。"""
    assert warming_cfg.WARMING_DRIVER in ("cloak", "roxy")
    assert warming_cfg.WARMING_INTERVAL_DAYS >= 1
    assert warming_cfg.WARMING_CONCURRENCY >= 1
    assert 0 <= warming_cfg.WARMING_COPY_PROBABILITY <= 1
    assert 0 <= warming_cfg.WARMING_UPVOTE_PROBABILITY <= 1

    cat, prompt = load_random_prompt()
    assert isinstance(cat, str) and len(cat) > 0
    assert isinstance(prompt, str) and len(prompt) > 0


def test_bezier_mouse_trajectory():
    """测试三阶贝塞尔鼠标轨迹生成算法。"""
    dummy_page = MagicMock()
    mouse = BezierMouse(dummy_page)
    p0 = (100.0, 100.0)
    p3 = (500.0, 400.0)
    points = mouse._generate_bezier_points(p0, p3, steps=25)

    assert len(points) == 25
    # 终点应接近目标点
    last_x, last_y = points[-1]
    assert abs(last_x - p3[0]) < 10.0
    assert abs(last_y - p3[1]) < 10.0

    # 验证平滑插值函数
    assert BezierMouse._ease_in_out(0.0) == 0.0
    assert BezierMouse._ease_in_out(1.0) == 1.0
    assert 0.4 < BezierMouse._ease_in_out(0.5) < 0.6


def test_active_hours_check():
    """测试时间窗口判断逻辑。"""
    assert warming_scheduler._is_within_active_hours("00:00-23:59") is True
    assert warming_scheduler._is_within_active_hours("") is True


def test_db_warming_cycle():
    """测试数据库层养号任务占用、状态流转与结果写回。"""
    # 写入测试账号
    test_email = "test_warming_acc@example.com"
    accounts = db._load_accounts()
    # 清理旧数据
    accounts = [a for a in accounts if a.get("email") != test_email]
    test_id = 999901
    accounts.append({
        "id": test_id,
        "email": test_email,
        "password": "FakePassword123!",
        "status": "success",
        "created_at": "2026-09-01T12:00:00",
        "updated_at": "2026-09-01T12:00:00",
    })
    db._save_accounts(accounts)

    try:
        # 1. 占用任务
        claimed = db.claim_account_warming(test_id, trigger="unit_test")
        assert claimed is True

        # 重复占用应当被拒绝
        assert db.claim_account_warming(test_id, trigger="unit_test") is False

        # 2. 标记运行中
        marked = db.mark_account_warming_running(test_id)
        assert marked is True

        acc = db.get_account(test_id)
        assert acc.get("warming_status") == "running"

        # 3. 写回养号成功结果
        fake_storage_state = {
            "cookies": [{"name": "oai_did", "value": "xyz123"}],
            "origins": [],
        }
        res = {
            "ok": True,
            "status": "success",
            "action": "natural_chat",
            "prompt": "测试提问",
            "dwell_seconds": 42.5,
            "storage_state": fake_storage_state,
        }
        updated = db.update_account_warming_result(test_id, res)
        assert updated is True

        acc_after = db.get_account(test_id)
        assert acc_after.get("warming_status") == "success"
        assert acc_after.get("warming_ok") is True
        assert acc_after.get("warm_count") == 1
        assert acc_after.get("last_warm_action") == "natural_chat"
        assert acc_after.get("storage_state") == fake_storage_state

        # 4. 再次养号增加次数
        db.claim_account_warming(test_id)
        db.update_account_warming_result(test_id, res)
        acc_second = db.get_account(test_id)
        assert acc_second.get("warm_count") == 2

    finally:
        # 清理测试数据
        clean_accounts = [a for a in db._load_accounts() if a.get("id") != test_id]
        db._save_accounts(clean_accounts)


def test_webui_warming_endpoints():
    """测试 WebUI 养号 API 端点响应。"""
    auth_code = "warming-test-auth"
    app = create_app(auth_code=auth_code)
    client = app.test_client()
    headers = {"X-Auth-Code": auth_code}

    # 1. 查询状态
    resp = client.get("/api/warming/status", headers=headers)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get("ok") is True
    assert "scheduler_enabled" in data["data"]
    assert "concurrency" in data["data"]

    # 2. 查询语料库
    resp_prompts = client.get("/api/warming/prompts", headers=headers)
    assert resp_prompts.status_code == 200
    if resp_prompts.headers.get("Content-Encoding") == "gzip":
        import gzip
        pdata = json.loads(gzip.decompress(resp_prompts.data).decode("utf-8"))
    else:
        pdata = resp_prompts.get_json()
    assert pdata.get("ok") is True
    assert "categories" in pdata["data"]

    # 3. 批量提交接口（空参数拦截）
    resp_invalid = client.post("/api/accounts/warm-bulk", json={"account_ids": []}, headers=headers)
    assert resp_invalid.status_code == 400
