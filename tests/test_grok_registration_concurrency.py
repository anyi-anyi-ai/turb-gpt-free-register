# -*- coding: utf-8 -*-
"""Grok 批量注册多 Worker 线程池并发调度与停止控制测试。"""
import time
import pytest
from core.grok.registration_service import GrokRegistrationManager


def test_concurrency_worker_dispatch(monkeypatch):
    manager = GrokRegistrationManager()
    # 模拟单个账号注册执行，统计同时并发的活跃数
    active_workers = 0
    max_active_workers = 0
    import threading
    lock = threading.Lock()

    def mock_register_single(*args, **kwargs):
        nonlocal active_workers, max_active_workers
        with lock:
            active_workers += 1
            if active_workers > max_active_workers:
                max_active_workers = active_workers
        time.sleep(0.1)
        with lock:
            active_workers -= 1
        return {"success": True, "email": "mock@example.com", "status": "healthy"}

    orig_sleep = time.sleep
    def mock_sleep(s):
        if s == 1:
            orig_sleep(0.01)
        else:
            orig_sleep(s)

    import core.grok.registration_service as reg_mod
    monkeypatch.setattr(reg_mod, "register_single_grok_account", mock_register_single)
    monkeypatch.setattr(reg_mod.time, "sleep", mock_sleep)

    # 启动 4 个任务，并发设为 2
    started = manager.start_batch(count=4, concurrency=2, run_quality_probe=False)
    assert started is True

    # 等待执行完毕
    for _ in range(60):
        if not manager.is_running:
            break
        orig_sleep(0.05)

    status = manager.get_status()
    assert status["is_running"] is False
    assert status["stats"]["total"] == 4
    assert status["stats"]["success"] == 4
    # 验证最大并发 worker 数达到 2
    assert max_active_workers == 2


def test_manager_stop_signal():
    manager = GrokRegistrationManager()
    manager._is_running = True
    manager._stop_event.clear()

    manager.stop()
    assert manager._stop_event.is_set()
    manager._is_running = False
