# -*- coding: utf-8 -*-
"""代理质量与前置欺诈度门禁 (Pre-flight IP Quality & Fraud Gate)

深度借鉴:
1. references/ip2proxy-python: 数据中心 (DCH) / 住宅 (RES) 分类标准
2. BlackHatWorld 注册机工程实践: 门禁熔断机制，杜绝脏 IP / 机房 IP 造成的算力与邮箱资源损耗
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

logger = logging.getLogger("proxy_quality")

# 缓存已检测的代理质量，避免同一粘性代理频繁发起外部探测 (TTL: 600秒)
_CACHE: Dict[str, Dict[str, Any]] = {}


def clear_proxy_quality_cache() -> None:
    _CACHE.clear()


def check_proxy_health_and_fraud(
    proxy_url: Optional[str] = None,
    timeout: float = 12.0,
) -> Dict[str, Any]:
    """通过指定的代理出口检测真实公网 IP、运营商 (ISP) 及机房/欺诈标签。

    返回字典:
    {
        "success": bool,
        "ip": str,
        "country": str,
        "isp": str,
        "is_datacenter": bool,
        "verdict": "CLEAN_RESIDENTIAL" | "DATACENTER_RISK" | "PROBE_FAILED",
        "error": str | None
    }
    """
    if proxy_url is None:
        try:
            from common import proxy_switch
            eff_proxy = proxy_switch.effective_proxy_url()
        except Exception:
            eff_proxy = None
    else:
        eff_proxy = proxy_url
    cache_key = eff_proxy or "DIRECT"

    now = time.time()
    if cache_key in _CACHE:
        cached_item = _CACHE[cache_key]
        if now - cached_item["cached_at"] < 60:
            return cached_item["data"]

    proxies = None
    if eff_proxy:
        proxies = {"http": eff_proxy, "https": eff_proxy}

    try:
        from curl_cffi import requests as c_requests

        # 优先使用原生 HTTPS 接口探测 (与双跳 CONNECT 隧道 100% 协议对齐)
        resp = None
        try:
            resp = c_requests.get(
                "https://ipwho.is/",
                proxies=proxies,
                timeout=timeout,
                impersonate="chrome124",
            )
        except Exception:
            pass

        if resp and resp.status_code == 200:
            data = resp.json()
            if data.get("success", False):
                ip = data.get("ip", "")
                country = data.get("country_code", "")
                isp = data.get("connection", {}).get("isp", "") or data.get("connection", {}).get("org", "")
                sec = data.get("security", {}) if isinstance(data.get("security"), dict) else {}
                KNOWN_HOSTING_KEYWORDS = (
                    "amazon", "aws", "microsoft", "azure", "google", "digitalocean",
                    "oracle", "alibaba", "tencent", "ovh", "hetzner", "linode", "vultr",
                    "choopa", "leaseweb", "fastly", "cloudflare", "akamai", "m247", "host", "server", "data"
                )
                isp_lower = isp.lower()
                is_hosting = bool(sec.get("hosting", False)) or any(kw in isp_lower for kw in KNOWN_HOSTING_KEYWORDS)
                is_proxy = bool(sec.get("proxy", False) or sec.get("vpn", False) or sec.get("tor", False))
                is_datacenter = is_hosting or is_proxy
                verdict = "DATACENTER_RISK" if is_datacenter else "CLEAN_RESIDENTIAL"

                result = {
                    "success": True,
                    "ip": ip,
                    "country": country,
                    "isp": isp,
                    "is_datacenter": is_datacenter,
                    "verdict": verdict,
                    "error": None,
                }
                _CACHE[cache_key] = {"data": result, "cached_at": now}
                return result

        # 备选回退: ip-api.com
        probe_url = "http://ip-api.com/json/?fields=status,message,country,countryCode,isp,org,as,query,hosting,proxy"
        resp = c_requests.get(
            probe_url,
            proxies=proxies,
            timeout=timeout,
            impersonate="chrome124",
        )
        if resp.status_code != 200:
            return {
                "success": False,
                "ip": "",
                "country": "",
                "isp": "",
                "is_datacenter": False,
                "verdict": "PROBE_FAILED",
                "error": f"HTTP {resp.status_code}",
            }

        data = resp.json()
        if data.get("status") != "success":
            return {
                "success": False,
                "ip": "",
                "country": "",
                "isp": "",
                "is_datacenter": False,
                "verdict": "PROBE_FAILED",
                "error": data.get("message", "Unknown IP API error"),
            }

        isp = data.get("isp", "") or data.get("org", "")
        KNOWN_HOSTING_KEYWORDS = (
            "amazon", "aws", "microsoft", "azure", "google", "digitalocean",
            "oracle", "alibaba", "tencent", "ovh", "hetzner", "linode", "vultr",
            "choopa", "leaseweb", "fastly", "cloudflare", "akamai", "m247", "host", "server", "data"
        )
        isp_lower = isp.lower()
        is_hosting = bool(data.get("hosting", False)) or any(kw in isp_lower for kw in KNOWN_HOSTING_KEYWORDS)
        is_proxy_flag = bool(data.get("proxy", False))
        ip = data.get("query", "")
        country = data.get("countryCode", "")

        is_datacenter = is_hosting
        verdict = "DATACENTER_RISK" if is_datacenter else "CLEAN_RESIDENTIAL"

        result = {
            "success": True,
            "ip": ip,
            "country": country,
            "isp": isp,
            "is_datacenter": is_datacenter,
            "verdict": verdict,
            "error": None,
        }

        _CACHE[cache_key] = {"data": result, "cached_at": now}
        return result

    except Exception as exc:
        logger.warning("⚠️ [IP 门禁] 探测出口代理质量异常 (%s): %s", eff_proxy or "Direct", exc)
        return {
            "success": False,
            "ip": "",
            "country": "",
            "isp": "",
            "is_datacenter": False,
            "verdict": "PROBE_FAILED",
            "error": str(exc),
        }


def log_and_audit_proxy_gate(proxy_url: Optional[str] = None) -> bool:
    """门禁审计输出：打印当前出口质量，并在出现机房/高风险 IP 时发出警报。"""
    res = check_proxy_health_and_fraud(proxy_url)
    if not res["success"]:
        logger.warning("🟡 [IP 门禁拦截] 代理连通性探测失败或超时 (%s)，拒绝并自动轮换...", res.get("error"))
        return False

    ip = res["ip"]
    country = res["country"]
    isp = res["isp"]
    verdict = res["verdict"]

    if verdict == "CLEAN_RESIDENTIAL":
        logger.info("🟢 [IP 门禁放行] 优质住宅 IP: %s (%s - %s)", ip, country, isp)
        return True
    else:
        logger.warning("🟡 [IP 门禁警报] 检测到机房/数据中心 IP: %s (%s - %s)，可能提高 Arkose/风控拦截率！", ip, country, isp)
        return False


def enforce_residential_gate(max_rotations: int = 3) -> bool:
    """主动熔断与自动换线门禁：
    如果当前出口被判定为机房/高风险 IP，主动尝试触发代理切换（如 Clash 节点切换），直至获取到住宅 IP 或用尽重试次数。
    """
    from common import proxy_switch

    for attempt in range(1, max_rotations + 1):
        quality = check_proxy_health_and_fraud()
        if not quality["success"]:
            logger.info("ℹ️ [IP 门禁] 连通性探测不可用，允许透传")
            return True

        if quality["verdict"] == "CLEAN_RESIDENTIAL":
            logger.info("🟢 [IP 门禁熔断就绪] 出口已切换为纯净住宅 IP: %s (%s)", quality["ip"], quality["isp"])
            return True

        logger.warning("🔄 [IP 门禁主动熔断] (尝试 %d/%d) 当前 IP %s 为机房节点，触发自动轮换切线...", attempt, max_rotations, quality["ip"])
        try:
            # 尝试通过 Clash 或平台调度器切线
            from common import clash_ctl
            clash_ctl.switch_to_next_node()
            time.sleep(2.0)
        except Exception as e:
            logger.debug("切线调度接口调用跳过: %s", e)
            break

    return False

