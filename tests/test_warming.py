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
    """测试养号配置、概率网格与语料库加载。"""
    assert warming_cfg.WARMING_DRIVER in ("cloak", "roxy")
    assert warming_cfg.WARMING_INTERVAL_DAYS >= 1
    assert warming_cfg.WARMING_CONCURRENCY >= 1
    assert warming_cfg.WARMING_COPY_PROBABILITY == 0.35
    assert warming_cfg.WARMING_UPVOTE_PROBABILITY == 0.25
    assert warming_cfg.WARMING_READ_SCROLL_PROBABILITY == 0.50
    assert warming_cfg.WARMING_PAST_CHAT_PROBABILITY == 0.15

    # 验证 Persona Matrix 权重分布
    p_weights = warming_cfg.PERSONA_WEIGHTS
    assert sum(p_weights.values()) == 100
    assert p_weights["workplace_efficiency"] == 25
    assert p_weights["student_lifelong_learning"] == 25
    assert p_weights["daily_lifestyle"] == 20
    assert p_weights["programmer_geek"] == 15
    assert p_weights["travel_and_culture"] == 15

    cat, prompt = load_random_prompt()
    assert isinstance(cat, str) and len(cat) > 0
    assert isinstance(prompt, str) and 15 <= len(prompt) <= 120


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


def test_semantic_chunk_typer():
    """测试语义词组分块打字机：CJK分块与英文分块。"""
    from core.account_warming import SemanticChunkTyper

    # 中文语义分块测试
    cjk_text = "请推荐几道简单、营养又好做的家常快手菜，最好半小时内搞定！"
    assert SemanticChunkTyper._is_cjk(cjk_text) is True
    chunks = SemanticChunkTyper._split_into_chunks(cjk_text)
    assert len(chunks) > 3
    # 验证分块拼接后内容无缺漏
    assert "".join(chunks) == cjk_text
    # 验证标点独立成块
    assert "、" in chunks
    assert "，" in chunks
    assert "！" in chunks

    # 英文分块测试
    en_text = "Can you recommend 3 simple ideas?"
    assert SemanticChunkTyper._is_cjk(en_text) is False
    en_chunks = SemanticChunkTyper._split_into_chunks(en_text)
    assert "".join(en_chunks) == en_text


def test_multilingual_country_routing():
    """测试基于 IP 出口国家的多语言画像与 1~3 轮连贯对话树抽取。"""
    from core.account_warming import load_random_prompt_tree, _resolve_language_by_country

    assert _resolve_language_by_country("US") == "en"
    assert _resolve_language_by_country("GB") == "en"
    assert _resolve_language_by_country("JP") == "ja"
    assert _resolve_language_by_country("CN") == "zh"
    assert _resolve_language_by_country("TW") == "zh_tw"
    assert _resolve_language_by_country("HK") == "zh_tw"

    # US 出口抽取英文
    lang, cat, prompt, followups, closings = load_random_prompt_tree(country="US")
    assert lang == "en"
    assert 15 <= len(prompt) <= 120
    assert isinstance(followups, list) and len(followups) > 0
    assert isinstance(closings, list) and len(closings) > 0

    # TW/HK 出口抽取繁体中文
    lang_tw, cat_tw, prompt_tw, followups_tw, closings_tw = load_random_prompt_tree(country="TW")
    assert lang_tw == "zh_tw"
    assert 15 <= len(prompt_tw) <= 120
    assert len(followups_tw) > 0

    # JP 出口抽取日语
    lang_ja, cat_ja, prompt_ja, followups_ja, closings_ja = load_random_prompt_tree(country="JP")
    assert lang_ja == "ja"
    assert 15 <= len(prompt_ja) <= 120
    assert len(followups_ja) > 0

    # CN 出口抽取简体中文
    lang_cn, cat_cn, prompt_cn, followups_cn, closings_cn = load_random_prompt_tree(country="CN")
    assert lang_cn == "zh"
    assert 15 <= len(prompt_cn) <= 120
    assert len(followups_cn) > 0


def test_persona_matrix_corpus_integrity():
    """测试语料库中所有画像覆盖度、字符长度约束（15~120字符）与多轮对话树完整性。"""
    from pathlib import Path
    import json

    corpus_path = Path("config/prompts_corpus.json")
    assert corpus_path.exists()
    data = json.loads(corpus_path.read_text(encoding="utf-8"))

    expected_personas = {
        "workplace_efficiency",
        "student_lifelong_learning",
        "daily_lifestyle",
        "programmer_geek",
        "travel_and_culture",
    }
    assert set(data["persona_weights"].keys()) == expected_personas

    # 检查各语言分支
    for lang, categories in data["languages"].items():
        assert lang in ("en", "ja", "zh_tw", "zh")
        for persona, items in categories.items():
            assert persona in expected_personas
            for item in items:
                prompt = item["prompt"]
                assert 15 <= len(prompt) <= 120, f"Prompt length violation in {lang}/{persona}: {prompt}"
                for f in item.get("followups", []):
                    assert 10 <= len(f) <= 120, f"Followup length violation in {lang}/{persona}: {f}"
                for c in item.get("closings", []):
                    assert 5 <= len(c) <= 60, f"Closing length violation in {lang}/{persona}: {c}"



def test_double_claim_deadlock_fixed():
    """测试解除二次 Claim 死锁：已处于 queued 状态的账号直接推进执行而不被拒绝。"""
    from core.account_warming import warm_single_account

    test_id = 999902
    test_email = "test_deadlock_fix@example.com"
    accounts = db._load_accounts()
    accounts = [a for a in accounts if a.get("id") != test_id]
    accounts.append({
        "id": test_id,
        "email": test_email,
        "status": "success",
        "warming_status": "queued",
        "created_at": "2026-09-01T12:00:00",
        "updated_at": "2026-09-01T12:00:00",
    })
    db._save_accounts(accounts)

    try:
        # 模拟工作线程中 AccountWarmingService.run 被 mock
        with patch("core.account_warming.AccountWarmingService") as MockService:
            mock_inst = MagicMock()
            mock_inst.run.return_value = {"ok": True, "status": "success"}
            MockService.return_value = mock_inst

            # 调用 warm_single_account，不应因处于 queued 而返回 "账号已在养号队列"
            res = warm_single_account(test_id, trigger="unit_test")
            assert res.get("ok") is True
            assert res.get("status") == "success"
            # 确认已经进入运行中并推进到 run()
            mock_inst.run.assert_called_once()
    finally:
        clean = [a for a in db._load_accounts() if a.get("id") != test_id]
        db._save_accounts(clean)


def test_plan_check_statuses_includes_warming():
    """测试轻量状态快照包含 warming_status 等字段，支持前端无感轮询。"""
    test_id = 999903
    test_email = "test_snapshot@example.com"
    accounts = db._load_accounts()
    accounts = [a for a in accounts if a.get("id") != test_id]
    accounts.append({
        "id": test_id,
        "email": test_email,
        "status": "success",
        "warming_status": "success",
        "warm_count": 5,
        "last_warmed_at": "2026-09-17T12:00:00",
        "created_at": "2026-09-01T12:00:00",
        "updated_at": "2026-09-01T12:00:00",
    })
    db._save_accounts(accounts)

    try:
        snapshot = db.list_account_plan_check_statuses(limit=5000)
        items = snapshot.get("items") or []
        target = next((item for item in items if int(item.get("id") or 0) == test_id), None)
        assert target is not None
        assert target.get("warming_status") == "success"
        assert target.get("warm_count") == 5
        assert target.get("last_warmed_at") == "2026-09-17T12:00:00"
    finally:
        clean = [a for a in db._load_accounts() if a.get("id") != test_id]
        db._save_accounts(clean)

