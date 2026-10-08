# -*- coding: utf-8 -*-
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import Mock

from core.flows.context import FlowContext, RegistrationResult
from core.flows.base import BaseRegistrationFlow
from core.flows.pool_helper import pick_unregistered_email_for_platform
from core.udio_register_service import UdioRegistrationFlow, pick_available_udio_account


class DummyFlow(BaseRegistrationFlow):
    platform_name = "dummy"
    platform_table = "dummy_accounts"

    def __init__(self, db_path=None):
        super().__init__(db_path=db_path)
        self.steps_executed = []

    def build_driver(self, context: FlowContext):
        self.steps_executed.append("build_driver")
        mock_driver = Mock()
        return mock_driver

    def execute(self, context: FlowContext) -> RegistrationResult:
        self.steps_executed.append("execute")
        return RegistrationResult(
            ok=True,
            email=context.email,
            platform=self.platform_name,
            access_token="tok_123",
            credits=5,
        )

    def persist(self, context: FlowContext, result: RegistrationResult) -> bool:
        self.steps_executed.append("persist")
        return True

    def push(self, context: FlowContext, result: RegistrationResult) -> bool:
        self.steps_executed.append("push")
        result.synced_to_gateway = True
        return True

    def cleanup(self, context: FlowContext) -> None:
        self.steps_executed.append("cleanup")
        super().cleanup(context)


def test_base_flow_lifecycle():
    flow = DummyFlow()
    account_info = {"email": "test@example.com", "payload": {}}
    res = flow.run(account_info=account_info, proxy="http://127.0.0.1:1080", headless=True)

    assert res.ok is True
    assert res.email == "test@example.com"
    assert res.access_token == "tok_123"
    assert res.synced_to_gateway is True
    # 验证生命周期执行顺序
    assert flow.steps_executed == ["build_driver", "execute", "persist", "push", "cleanup"]


def test_base_flow_handles_exception_and_cleans_up():
    class FailingFlow(DummyFlow):
        def execute(self, context: FlowContext) -> RegistrationResult:
            self.steps_executed.append("execute")
            raise RuntimeError("页面渲染超时")

    flow = FailingFlow()
    account_info = {"email": "fail@example.com"}
    res = flow.run(account_info=account_info)

    assert res.ok is False
    assert res.email == "fail@example.com"
    assert "页面渲染超时" in res.error
    # 即使抛出异常，cleanup 也必须在 finally 中被调用
    assert "cleanup" in flow.steps_executed


def test_pick_unregistered_email_for_platform(tmp_path: Path):
    db_file = tmp_path / "test_pool.sqlite3"
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE email_pool (id INTEGER PRIMARY KEY, email TEXT, payload TEXT);")
        conn.execute("CREATE TABLE dummy_accounts (id INTEGER PRIMARY KEY, email TEXT);")
        conn.execute("INSERT INTO email_pool (email, payload) VALUES ('used@test.com', '{}');")
        conn.execute("INSERT INTO email_pool (email, payload) VALUES ('fresh@test.com', '{\"pwd\": \"123\"}');")
        conn.execute("INSERT INTO dummy_accounts (email) VALUES ('used@test.com');")
        conn.commit()

    # 自动挑选未在 dummy_accounts 注册过的 fresh@test.com
    acc = pick_unregistered_email_for_platform("dummy_accounts", db_path=db_file)
    assert acc["email"] == "fresh@test.com"
    assert acc["payload"]["pwd"] == "123"
    assert acc["source"] == "email_pool"

    # 指定邮箱
    specific = pick_unregistered_email_for_platform("dummy_accounts", specific_email="any@test.com", db_path=db_file)
    assert specific["email"] == "any@test.com"
    assert specific["source"] == "manual"


def test_suno_flow_meta_and_compatibility(tmp_path: Path):
    from core.suno_register_service import SunoRegistrationFlow, pick_available_suno_account

    flow = SunoRegistrationFlow()
    assert flow.platform_name == "suno"
    assert flow.platform_table == "suno_accounts"

    db_file = tmp_path / "test_suno.sqlite3"
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE email_pool (id INTEGER PRIMARY KEY, email TEXT, payload TEXT);")
        conn.execute("CREATE TABLE suno_accounts (id INTEGER PRIMARY KEY, email TEXT);")
        conn.execute("INSERT INTO email_pool (email, payload) VALUES ('suno_candidate@test.com', '{\"password\": \"TestPass123!\"}');")
        conn.commit()

    picked = pick_available_suno_account(db_path=db_file)
    assert picked["email"] == "suno_candidate@test.com"
    assert picked["password"] == "TestPass123!"
    assert "full_name" in picked


def test_elevenlabs_flow_meta_and_password(tmp_path: Path):
    from core.elevenlabs_register_service import (
        ElevenLabsRegistrationFlow,
        pick_available_elevenlabs_account,
        _generate_secure_password,
    )

    flow = ElevenLabsRegistrationFlow()
    assert flow.platform_name == "elevenlabs"
    assert flow.platform_table == "elevenlabs_accounts"

    pwd = _generate_secure_password()
    assert len(pwd) >= 16
    assert pwd.startswith("Eleven")
    assert pwd.endswith("#2026!")

    db_file = tmp_path / "test_eleven.sqlite3"
    with sqlite3.connect(db_file) as conn:
        conn.execute("CREATE TABLE email_pool (id INTEGER PRIMARY KEY, email TEXT, payload TEXT);")
        conn.execute("INSERT INTO email_pool (email, payload) VALUES ('el_candidate@test.com', '{\"pwd\": \"abc\"}');")
        conn.commit()

    picked = pick_available_elevenlabs_account(db_path=db_file)
    assert picked["email"] == "el_candidate@test.com"

