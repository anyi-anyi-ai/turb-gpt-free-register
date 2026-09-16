# -*- coding: utf-8 -*-
"""
代理节点统计与住宅宽带识别模块 (Proxy Stats & Residential Detection)

功能：
1. 从本地 Docker Resin 代理网关拉取节点存活、断路熔断及路由数据。
2. 批量识别各国家节点的出口 IP 属性（住宅宽带 vs 机房/Hosting），结合 SQLite 本地缓存避免重复外部查询。
3. 聚合计算各国家的：
   - 干净可用节点数 (Clean Nodes)
   - 住宅宽带节点与独立住宅 IP 数 (Residential Nodes & IPs)
   - 当前配置的流量百分比权重 (Weight %)
4. 提供基于权重的目标国家抽取算法，供注册调度器无缝分流。
"""
import json
import logging
import os
import random
import re
import threading
import time
import urllib.request
from typing import Any

from core import db
import config.proxy as proxy_cfg

logger = logging.getLogger(__name__)

RESIN_API_BASE = os.getenv("RESIN_API_BASE", "http://127.0.0.1:2260").rstrip("/")
RESIN_ADMIN_TOKEN = os.getenv("RESIN_ADMIN_TOKEN", "resin_admin_a9e2f47c81d3b06e92fa184c")

COUNTRY_META: dict[str, dict[str, str]] = {
    "JP": {"name": "日本", "flag": "🇯🇵"},
    "GB": {"name": "英国", "flag": "🇬🇧"},
    "US": {"name": "美国", "flag": "🇺🇸"},
    "DE": {"name": "德国", "flag": "🇩🇪"},
    "SG": {"name": "新加坡", "flag": "🇸🇬"},
    "CA": {"name": "加拿大", "flag": "🇨🇦"},
    "NL": {"name": "荷兰", "flag": "🇳🇱"},
    "FR": {"name": "法国", "flag": "🇫🇷"},
    "AU": {"name": "澳大利亚", "flag": "🇦🇺"},
}

_CACHE_LOCK = threading.Lock()
_LAST_STATS_CACHE: list[dict[str, Any]] = []
_LAST_STATS_TIME: float = 0.0
_STATS_CACHE_TTL = 30.0  # 缓存 30 秒


def _resin_request(path: str, method: str = "GET", body: dict | None = None, timeout: float = 8.0) -> dict | None:
    """向本地 Resin API 发送认证请求。"""
    url = f"{RESIN_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {RESIN_ADMIN_TOKEN}",
        "Content-Type": "application/json",
    }
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            content = resp.read().decode("utf-8")
            return json.loads(content) if content else {}
    except Exception as exc:
        logger.debug("[ProxyStats] Resin 请求失败 %s %s: %s", method, path, exc)
        return None


def preload_ip_intelligence(ip_list: list[str]) -> None:
    """批量查询并填充 IP 属性缓存（利用 ip-api.com/batch 单次查 100 个，支持 IPv4 与 IPv6）。"""
    needed = []
    for ip in set(ip_list):
        ip = str(ip or "").strip()
        if not ip:
            continue
        if db.get_cached_ip_intelligence(ip) is None:
            needed.append(ip)

    if not needed:
        return

    # 按 100 个一组分块请求
    chunk_size = 100
    for i in range(0, len(needed), chunk_size):
        chunk = needed[i:i + chunk_size]
        try:
            req = urllib.request.Request(
                "http://ip-api.com/batch?fields=status,countryCode,isp,org,hosting,query",
                data=json.dumps(chunk).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=8.0) as resp:
                results = json.loads(resp.read().decode("utf-8"))
                for item in results:
                    q_ip = item.get("query")
                    if q_ip and item.get("status") == "success":
                        is_res = (item.get("hosting") is False)
                        db.save_cached_ip_intelligence(
                            q_ip,
                            is_res,
                            isp=str(item.get("isp") or ""),
                            org=str(item.get("org") or ""),
                            country=str(item.get("countryCode") or "")
                        )
                    elif q_ip:
                        db.save_cached_ip_intelligence(q_ip, False)
        except Exception as e:
            logger.debug("[ProxyStats] 批量 IP 属性查询失败: %s", e)
            for ip in chunk:
                db.save_cached_ip_intelligence(ip, False)


def is_ip_residential(ip: str) -> bool:
    """查询单 IP 是否属于住宅宽带（非机房 hosting，支持 IPv4/IPv6）。"""
    ip = str(ip or "").strip()
    if not ip:
        return False
    cached = db.get_cached_ip_intelligence(ip)
    if cached is not None:
        return bool(cached.get("is_residential"))
    preload_ip_intelligence([ip])
    cached = db.get_cached_ip_intelligence(ip)
    return bool(cached.get("is_residential")) if cached else False


def is_residential_only() -> bool:
    """是否开启了纯住宅模式（即关闭普通干净节点，仅保留住宅宽带节点参与轮询）。"""
    return db.is_proxy_residential_only()


def apply_residential_routing(enabled: bool) -> dict[str, Any]:
    """切换纯住宅模式。
    
    1. enabled=True (关闭干净节点):
       各国家平台仅保留经 IP 检测判定的住宅宽带节点参与路由，机房/Hosting 节点不参与轮询。
    2. enabled=False (开启干净节点):
       所有干净存活节点重新加入轮询池，regex_filters 恢复为 ['.*']。
    """
    db.set_proxy_residential_only(enabled)

    configured_pools = getattr(proxy_cfg, "PROXY_COUNTRY_POOLS", {}) or {}
    configured_countries = [c.upper() for c in configured_pools.keys()]
    if not configured_countries:
        configured_countries = ["JP", "GB", "US", "DE", "SG"]

    nodes_data = _resin_request("/api/v1/nodes?limit=3500") or {}
    all_nodes = nodes_data.get("items", [])

    platforms_data = _resin_request("/api/v1/platforms") or {}
    platforms = {p.get("name", "").upper(): p for p in platforms_data.get("items", [])}

    all_clean_ips = []
    for n in all_nodes:
        if n.get("enabled") and n.get("has_outbound") and n.get("circuit_open_since") is None:
            egress = str(n.get("egress_ip") or "").strip()
            if egress:
                all_clean_ips.append(egress)
    preload_ip_intelligence(all_clean_ips)

    warnings: list[str] = []
    applied_countries: list[str] = []

    for country in configured_countries:
        c_low = country.lower()
        platform = platforms.get(country)
        if not platform:
            logger.warning("[ProxyStats] Resin 中未找到平台: %s", country)
            continue
        pid = platform.get("id")
        if not pid:
            continue

        if enabled:
            c_nodes = [
                n for n in all_nodes
                if (n.get("region") or n.get("display_country") or "").lower() == c_low
                and n.get("enabled") and n.get("has_outbound") and n.get("circuit_open_since") is None
            ]
            res_nodes = [n for n in c_nodes if is_ip_residential(n.get("egress_ip"))]

            tags: list[str] = []
            for n in res_nodes:
                for t in n.get("tags", []):
                    tag_str = t.get("tag")
                    if tag_str:
                        tags.append(re.escape(tag_str))

            if tags:
                rgx = ["^(" + "|".join(sorted(set(tags))) + ")$"]
                _resin_request(f"/api/v1/platforms/{pid}", method="PATCH", body={"regex_filters": rgx})
                applied_countries.append(country)
            else:
                warnings.append(f"{country} 未发现有效住宅宽带节点，已保持默认全量轮询")
                _resin_request(f"/api/v1/platforms/{pid}", method="PATCH", body={"regex_filters": [".*"]})
        else:
            _resin_request(f"/api/v1/platforms/{pid}", method="PATCH", body={"regex_filters": [".*"]})
            applied_countries.append(country)

    with _CACHE_LOCK:
        global _LAST_STATS_TIME
        _LAST_STATS_TIME = 0.0

    items = get_country_proxy_stats(force_refresh=True, skip_res_sync=True)
    return {
        "ok": True,
        "residential_only": enabled,
        "applied_countries": applied_countries,
        "warnings": warnings,
        "items": items,
    }


def get_country_proxy_stats(force_refresh: bool = False, skip_res_sync: bool = False) -> list[dict[str, Any]]:
    """获取所有已配置国家的干净节点与住宅宽带统计数据。"""
    global _LAST_STATS_CACHE, _LAST_STATS_TIME

    with _CACHE_LOCK:
        now = time.time()
        if not force_refresh and _LAST_STATS_CACHE and (now - _LAST_STATS_TIME < _STATS_CACHE_TTL):
            return _LAST_STATS_CACHE

    # 1. 获取已配置的国家列表（从 PROXY_COUNTRY_POOLS）
    configured_pools = getattr(proxy_cfg, "PROXY_COUNTRY_POOLS", {}) or {}
    configured_countries = [c.upper() for c in configured_pools.keys()]
    if not configured_countries:
        configured_countries = ["JP", "GB", "US", "DE", "SG"]

    # 2. 读取当前已持久化的百分比权重
    saved_weights = db.get_proxy_country_weights()
    default_c = str(getattr(proxy_cfg, "DEFAULT_REGISTER_COUNTRY", "") or "").strip().upper()
    is_res_only = is_residential_only()

    # 3. 从 Resin 拉取全部节点及平台
    nodes_data = _resin_request("/api/v1/nodes?limit=3500") or {}
    all_nodes = nodes_data.get("items", [])

    platforms_data = _resin_request("/api/v1/platforms") or {}
    platforms = {p.get("name", "").upper(): p for p in platforms_data.get("items", [])}

    # 如果持久化了纯住宅模式，但 Resin 平台规则被意外重置为 [".*"]，进行自愈同步
    if not skip_res_sync and is_res_only and platforms:
        needs_sync = any(
            platforms.get(c, {}).get("regex_filters") == [".*"]
            for c in configured_countries
            if c in platforms
        )
        if needs_sync:
            try:
                sync_res = apply_residential_routing(True)
                return sync_res.get("items", [])
            except Exception as sync_err:
                logger.debug("[ProxyStats] 纯住宅规则同步失败: %s", sync_err)

    # 4. 收集所有 clean 节点的出口 IP 并进行批量预热
    all_clean_ips = []
    for n in all_nodes:
        if n.get("enabled") and n.get("has_outbound") and n.get("circuit_open_since") is None:
            egress = str(n.get("egress_ip") or "").strip()
            if egress:
                all_clean_ips.append(egress)

    preload_ip_intelligence(all_clean_ips)

    # 5. 分国家统计
    results = []
    total_assigned_weight = sum(saved_weights.get(c, 0.0) for c in configured_countries)

    for country in configured_countries:
        meta = COUNTRY_META.get(country, {"name": country, "flag": "🌐"})
        c_low = country.lower()

        country_nodes = [
            n for n in all_nodes
            if (n.get("region") or n.get("display_country") or "").lower() == c_low
        ]
        total_nodes = len(country_nodes)

        clean_nodes = [
            n for n in country_nodes
            if n.get("enabled") and n.get("has_outbound") and n.get("circuit_open_since") is None
        ]

        distinct_ips: set[str] = set()
        residential_ips: set[str] = set()
        residential_nodes_count = 0

        for n in clean_nodes:
            egress = str(n.get("egress_ip") or "").strip()
            if egress:
                distinct_ips.add(egress)
                if is_ip_residential(egress):
                    residential_ips.add(egress)
                    residential_nodes_count += 1

        routable_nodes = platforms.get(country, {}).get("routable_node_count", len(clean_nodes))

        if total_assigned_weight > 0:
            weight = saved_weights.get(country, 0.0)
        else:
            if default_c:
                weight = 100.0 if country == default_c else 0.0
            else:
                weight = round(100.0 / max(1, len(configured_countries)), 1)

        results.append({
            "country": country,
            "name": meta["name"],
            "flag": meta["flag"],
            "total_nodes": total_nodes,
            "clean_nodes": len(clean_nodes),
            "residential_nodes": residential_nodes_count,
            "distinct_ips": len(distinct_ips),
            "residential_ips": len(residential_ips),
            "routable_nodes": routable_nodes,
            "weight": weight,
            "residential_only": is_res_only,
        })

    with _CACHE_LOCK:
        _LAST_STATS_CACHE = results
        _LAST_STATS_TIME = time.time()

    return results


def pick_weighted_country() -> str:
    """根据前端配置的国家百分比权重，智能抽取一个目标国家。
    
    若未设置权重或所有权重为 0，回退到 DEFAULT_REGISTER_COUNTRY 或可用国家列表。
    """
    configured_pools = getattr(proxy_cfg, "PROXY_COUNTRY_POOLS", {}) or {}
    configured_countries = [c.upper() for c in configured_pools.keys()]
    if not configured_countries:
        return str(getattr(proxy_cfg, "DEFAULT_REGISTER_COUNTRY", "JP") or "JP").upper()

    weights_map = db.get_proxy_country_weights()
    weights = [max(0.0, float(weights_map.get(c, 0.0))) for c in configured_countries]

    total_w = sum(weights)
    if total_w <= 0:
        def_c = str(getattr(proxy_cfg, "DEFAULT_REGISTER_COUNTRY", "") or "").strip().upper()
        if def_c in configured_countries:
            return def_c
        return random.choice(configured_countries)

    chosen = random.choices(configured_countries, weights=weights, k=1)[0]
    return chosen
