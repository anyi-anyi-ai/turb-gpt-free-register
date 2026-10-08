# -*- coding: utf-8 -*-
"""代理与网络中心路由 (Network & Proxy Router)。

整合 Clash 节点管理、连通探测与国家分流/住宅权重分配大盘。
"""
import logging
from typing import Any, Dict, Optional
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from core import db

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/proxy", tags=["network"])


class CountryWeightsPayload(BaseModel):
    weights: Dict[str, Any]


class ResidentialTogglePayload(BaseModel):
    enabled: Optional[bool] = None


@router.get("/country-stats")
def api_proxy_country_stats(refresh: bool = Query(False, description="强制刷新")):
    """获取代理国家节点分布统计、权重及住宅独享模式状态。"""
    try:
        from core import proxy_stats
        items = proxy_stats.get_country_proxy_stats(force_refresh=refresh)
        weights = getattr(db, "get_proxy_country_weights", lambda: {})()
        res_only = proxy_stats.is_residential_only() if hasattr(proxy_stats, "is_residential_only") else False
        return {
            "ok": True,
            "items": items,
            "weights": weights,
            "residential_only": res_only,
        }
    except Exception as e:
        logger.exception("获取代理国家统计失败")
        return {"ok": False, "error": str(e)}


@router.post("/country-weights")
def api_proxy_country_weights(payload: CountryWeightsPayload):
    """保存代理国家分流权重配置。"""
    try:
        from core import proxy_stats
        save_fn = getattr(db, "save_proxy_country_weights", None)
        if save_fn:
            save_fn(payload.weights)
        if hasattr(proxy_stats, "_CACHE_LOCK") and hasattr(proxy_stats, "_LAST_STATS_TIME"):
            with proxy_stats._CACHE_LOCK:
                proxy_stats._LAST_STATS_TIME = 0.0
        weights = getattr(db, "get_proxy_country_weights", lambda: {})()
        res_only = proxy_stats.is_residential_only() if hasattr(proxy_stats, "is_residential_only") else False
        return {
            "ok": True,
            "weights": weights,
            "residential_only": res_only,
        }
    except Exception as e:
        logger.exception("保存代理国家权重失败")
        return {"ok": False, "error": str(e)}


@router.post("/refresh-stats")
def api_proxy_refresh_stats():
    """手动刷新代理国家分布节点统计。"""
    try:
        from core import proxy_stats
        items = proxy_stats.get_country_proxy_stats(force_refresh=True)
        weights = getattr(db, "get_proxy_country_weights", lambda: {})()
        res_only = proxy_stats.is_residential_only() if hasattr(proxy_stats, "is_residential_only") else False
        return {
            "ok": True,
            "items": items,
            "weights": weights,
            "residential_only": res_only,
        }
    except Exception as e:
        logger.exception("刷新代理国家统计失败")
        return {"ok": False, "error": str(e)}


@router.post("/toggle-residential-only")
def api_proxy_toggle_residential_only(payload: Optional[ResidentialTogglePayload] = None):
    """切换纯住宅代理独享模式开关。"""
    try:
        from core import proxy_stats
        toggle_fn = getattr(proxy_stats, "toggle_residential_only", None)
        if toggle_fn:
            is_enabled = payload.enabled if payload and payload.enabled is not None else None
            new_val = toggle_fn(is_enabled)
            return {"ok": True, "residential_only": new_val}
        return {"ok": True, "residential_only": False}
    except Exception as e:
        logger.exception("切换纯住宅代理模式失败")
        return {"ok": False, "error": str(e)}
