# -*- coding: utf-8 -*-
"""ChatGPT 账号养号调度器与后台并发执行队列。"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from config import warming as _cfg
from core import db
from core.account_warming import warm_single_account

logger = logging.getLogger(__name__)

_EXECUTOR: ThreadPoolExecutor | None = None
_RUNNING_IDS: set[int] = set()
_LOCK = threading.Lock()
_SCHEDULER_THREAD: threading.Thread | None = None
_STOP_EVENT = threading.Event()


def _is_within_active_hours(window_str: str) -> bool:
    """判断当前时间是否处于允许养号的日间时间窗口（如 '08:00-23:00'）。"""
    if not window_str or "-" not in window_str:
        return True
    try:
        parts = window_str.split("-")
        start_h, start_m = map(int, parts[0].strip().split(":"))
        end_h, end_m = map(int, parts[1].strip().split(":"))
        now = datetime.now()
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        curr_min = now.hour * 60 + now.minute
        return start_min <= curr_min <= end_min
    except Exception:
        return True


def _get_executor() -> ThreadPoolExecutor:
    global _EXECUTOR
    with _LOCK:
        concurrency = max(1, int(getattr(_cfg, "WARMING_CONCURRENCY", 1) or 1))
        if _EXECUTOR is None:
            _EXECUTOR = ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="warming-worker")
        return _EXECUTOR


def _worker_wrapper(account_id: int, trigger: str, headless: bool | None = None) -> None:
    with _LOCK:
        _RUNNING_IDS.add(account_id)
    try:
        warm_single_account(account_id, trigger=trigger, headless=headless)
    except Exception as exc:
        logger.error("[WarmingScheduler] 养号任务执行异常: acc_id=%s err=%s", account_id, exc)
    finally:
        with _LOCK:
            _RUNNING_IDS.discard(account_id)


def trigger_batch_warming(account_ids: list[int], *, headless: bool | None = None) -> dict:
    """手动批量提交养号任务。"""
    executor = _get_executor()
    accepted = []
    skipped = []

    for aid in account_ids:
        aid = int(aid)
        with _LOCK:
            if aid in _RUNNING_IDS:
                skipped.append(aid)
                continue
        if db.claim_account_warming(aid, trigger="manual"):
            accepted.append(aid)
            executor.submit(_worker_wrapper, aid, "manual", headless)
        else:
            skipped.append(aid)

    return {
        "ok": True,
        "total": len(account_ids),
        "submitted": len(accepted),
        "skipped": len(skipped),
        "accepted_ids": accepted,
        "skipped_ids": skipped,
    }


def _scheduler_loop() -> None:
    """后台定时扫描与自动调度循环。"""
    logger.info("[WarmingScheduler] 定时养号调度后台线程已启动")
    while not _STOP_EVENT.is_set():
        try:
            enabled = bool(getattr(_cfg, "WARMING_SCHEDULER_ENABLED", False))
            active_hours = str(getattr(_cfg, "WARMING_ACTIVE_HOURS", "08:00-23:00"))
            interval_days = int(getattr(_cfg, "WARMING_INTERVAL_DAYS", 3) or 3)
            concurrency = max(1, int(getattr(_cfg, "WARMING_CONCURRENCY", 1) or 1))

            if enabled and _is_within_active_hours(active_hours):
                with _LOCK:
                    available_slots = max(0, concurrency - len(_RUNNING_IDS))

                if available_slots > 0:
                    candidates = db.query_accounts_need_warming(interval_days=interval_days, limit=available_slots)
                    if candidates:
                        logger.info("[WarmingScheduler] 发现 %d 个到期待养号账号，正在调度执行...", len(candidates))
                        executor = _get_executor()
                        for acc in candidates:
                            aid = int(acc.get("id") or 0)
                            if aid and db.claim_account_warming(aid, trigger="scheduled"):
                                executor.submit(_worker_wrapper, aid, "scheduled", None)

        except Exception as exc:
            logger.error("[WarmingScheduler] 调度循环异常: %s", exc)

        # 每隔 5 分钟轮询一次
        for _ in range(30):
            if _STOP_EVENT.is_set():
                break
            time.sleep(10)

    logger.info("[WarmingScheduler] 定时养号调度后台线程已停止")


def start_scheduler() -> None:
    """启动全局后台调度器。"""
    global _SCHEDULER_THREAD, _STOP_EVENT
    with _LOCK:
        if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
            return
        _STOP_EVENT.clear()
        _SCHEDULER_THREAD = threading.Thread(target=_scheduler_loop, daemon=True, name="warming-scheduler")
        _SCHEDULER_THREAD.start()


def stop_scheduler() -> None:
    """停止后台调度器。"""
    global _STOP_EVENT
    _STOP_EVENT.set()


def is_scheduler_running() -> bool:
    with _LOCK:
        return bool(_SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive() and not _STOP_EVENT.is_set())


def get_warming_status() -> dict:
    """获取当前养号引擎运行状态。"""
    with _LOCK:
        running_count = len(_RUNNING_IDS)
        running_ids = list(_RUNNING_IDS)
    return {
        "scheduler_enabled": bool(getattr(_cfg, "WARMING_SCHEDULER_ENABLED", False)),
        "scheduler_active": is_scheduler_running(),
        "active_hours": getattr(_cfg, "WARMING_ACTIVE_HOURS", "08:00-23:00"),
        "interval_days": getattr(_cfg, "WARMING_INTERVAL_DAYS", 3),
        "concurrency": getattr(_cfg, "WARMING_CONCURRENCY", 1),
        "running_count": running_count,
        "running_account_ids": running_ids,
    }
