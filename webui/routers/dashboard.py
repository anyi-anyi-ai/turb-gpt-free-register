# -*- coding: utf-8 -*-
"""大盘总览与监控路由 (Dashboard Router)。"""
import os
import time
from typing import Any, Dict
from fastapi import APIRouter

from core import vault_service

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


def _get_active_runs_count() -> int:
    try:
        from webui import server
        runs = getattr(server, "RUNS", {})
        active = 0
        for r in runs.values():
            proc = r.get("process")
            if proc and proc.poll() is None:
                active += 1
        return active
    except Exception:
        return 0


@router.get("/overview")
def api_dashboard_overview() -> Dict[str, Any]:
    """大盘综合概览数据：资产总览、任务队列水位、网络模式与系统状态。"""
    vault_metrics = vault_service.get_vault_metrics()
    active_runs = _get_active_runs_count()

    # 读取当前配置的网络与代理模式
    proxy_mode = os.environ.get("PROXY_MODE") or os.environ.get("REG_FACTORY_PROXY_MODE") or "direct"
    residential_mode = os.environ.get("REG_FACTORY_RESIDENTIAL_TRAFFIC_MODE") or "false"

    return {
        "ok": True,
        "timestamp": int(time.time()),
        "vault": vault_metrics,
        "runtime": {
            "active_tasks": active_runs,
            "proxy_mode": proxy_mode,
            "residential_only": residential_mode.lower() in ("true", "1", "yes"),
        },
    }
