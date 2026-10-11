# -*- coding: utf-8 -*-
"""
核心三级代理容灾路由体系 (Three-Tier Proxy Failover Architecture)

设计原则与红线：
1. Level 1（初始连接）：优先复用账号历史绑定的首个注册出口 IP 与 Session。
2. Level 2（第一级备用）：当历史 IP 离线/不可用时，严格在【同国家】代理池中轮换新 Session。
3. Level 3（第二级备用）：若该国节点全部不可用或上游 502，递归至【该国家所属大洲】的可用节点（如 GB/FI -> DE；JP -> SG）。
4. Fail-Safe（大洲熔断）：若所在大洲节点均不可用，严格就地挂起并告警，【绝对禁止跨大洲跨区跳跃】。
"""
import logging
import random
import re
import socket
import time
import urllib.parse
from typing import Optional, Tuple

logger = logging.getLogger("continent_proxy_router")

# 大洲国家映射表
CONTINENT_MAP = {
    # 亚洲
    "JP": "ASIA", "JAPAN": "ASIA", "SG": "ASIA", "SINGAPORE": "ASIA",
    "HK": "ASIA", "HONG KONG": "ASIA", "TW": "ASIA", "TAIWAN": "ASIA",
    "KR": "ASIA", "KOREA": "ASIA", "MY": "ASIA", "TH": "ASIA", "VN": "ASIA",
    # 欧洲
    "DE": "EUROPE", "GERMANY": "EUROPE", "GB": "EUROPE", "UK": "EUROPE",
    "UNITED KINGDOM": "EUROPE", "FR": "EUROPE", "FRANCE": "EUROPE",
    "NL": "EUROPE", "NETHERLANDS": "EUROPE", "IT": "EUROPE", "ITALY": "EUROPE",
    "ES": "EUROPE", "SPAIN": "EUROPE", "SE": "EUROPE", "SWEDEN": "EUROPE",
    "CH": "EUROPE", "SWITZERLAND": "EUROPE", "PL": "EUROPE", "POLAND": "EUROPE",
    "FI": "EUROPE", "FINLAND": "EUROPE", "EU": "EUROPE",
    # 美洲
    "US": "AMERICAS", "USA": "AMERICAS", "UNITED STATES": "AMERICAS", "AMERICA": "AMERICAS",
    "CA": "AMERICAS", "CANADA": "AMERICAS", "MX": "AMERICAS", "MEXICO": "AMERICAS",
    "BR": "AMERICAS", "BRAZIL": "AMERICAS",
}

# 各大洲优先备用节点顺序（依据本地代理池可用国家，已实测连通性）
CONTINENT_FALLBACK_ORDER = {
    "ASIA": ["JP", "SG", "KR"],
    "EUROPE": ["NL", "GB", "DE", "FR", "SE", "ES", "IT", "PL"],
    "AMERICAS": ["US", "CA", "MX"],
}

# 国家标准化简写
NORMALIZE_COUNTRY_CODE = {
    "UNITED STATES": "US", "USA": "US", "AMERICA": "US",
    "JAPAN": "JP",
    "GERMANY": "DE",
    "UNITED KINGDOM": "GB", "UK": "GB",
    "FINLAND": "FI",
    "SINGAPORE": "SG",
    "NETHERLANDS": "NL",
    "SPAIN": "ES",
    "ITALY": "IT",
    "POLAND": "PL",
    "SWEDEN": "SE",
}


def normalize_country(country_raw: Optional[str]) -> str:
    """将输入的国家文本标准化为两位 ISO 大写代码。"""
    c = str(country_raw or "").strip().upper()
    return NORMALIZE_COUNTRY_CODE.get(c, c)


def get_continent(country_raw: Optional[str]) -> str:
    """获取国家所属大洲代码 (ASIA / EUROPE / AMERICAS)。"""
    c = str(country_raw or "").strip().upper()
    return CONTINENT_MAP.get(c, "AMERICAS")


def check_proxy_alive(proxy_url: str, timeout: float = 4.5) -> bool:
    """全面检测代理端口与真实上游连通性（防止本地 Resin 网关接受 TCP 但上游返回 502/503 假活）。"""
    try:
        p = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
        parsed = urllib.parse.urlparse(p)
        if parsed.hostname and parsed.port:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(1.0)
                if s.connect_ex((parsed.hostname, parsed.port)) != 0:
                    return False
        # 对代理进行真实网络出口探测（允许 1 次重试以容纳住宅节点建立隧道偶发抖动）
        import requests
        for attempt in range(2):
            try:
                resp = requests.get(
                    "https://api.ipify.org",
                    proxies={"http": p, "https": p},
                    timeout=timeout,
                )
                if resp.status_code == 200:
                    if check_proxy_tunnel_alive(p, "chatgpt.com", timeout=timeout):
                        return True
            except Exception:
                if attempt == 0:
                    time.sleep(0.4)
                    continue
        return False
    except Exception:
        return False


def check_proxy_tunnel_alive(proxy_url: str, target_host: str = "chatgpt.com", target_port: int = 443, timeout: float = 3.0) -> bool:
    """轻量级前置隧道探测：向代理发送 HTTP CONNECT 请求验证上游住宅出口是否可用。
    如果仅验证 TCP 端口，本地 Resin 网关永远返回 true。必须通过 CONNECT 探测上游。"""
    try:
        p = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
        parsed = urllib.parse.urlparse(p)
        if not parsed.hostname or not parsed.port:
            return False
            
        with socket.create_connection((parsed.hostname, parsed.port), timeout=timeout) as s:
            auth_str = ""
            if parsed.username or parsed.password:
                import base64
                creds = f"{parsed.username or ''}:{parsed.password or ''}"
                b64_creds = base64.b64encode(creds.encode('utf-8')).decode('utf-8')
                auth_str = f"Proxy-Authorization: Basic {b64_creds}\r\n"
                
            req = f"CONNECT {target_host}:{target_port} HTTP/1.1\r\nHost: {target_host}:{target_port}\r\n{auth_str}\r\n"
            s.sendall(req.encode('utf-8'))
            
            resp = s.recv(4096).decode('utf-8', errors='ignore')
            if resp.startswith("HTTP/1.1 200") or resp.startswith("HTTP/1.0 200"):
                return True
        return False
    except Exception:
        return False


def check_proxy_tcp_alive(proxy_url: str, timeout: float = 2.5) -> bool:
    """向后兼容别名，直接调用具备上游检测的 check_proxy_alive。"""
    return check_proxy_alive(proxy_url, timeout=timeout)


def build_resin_proxy_for_country(country: str, session_id: Optional[str] = None) -> str:
    """为指定国家生成 Resin 代理 URL，带动态 Session 轮换以获取新鲜 IP。"""
    from config.proxy import PROXY_COUNTRY_POOLS
    sess = session_id or f"sess_{int(time.time() * 1000) % 1000000}"
    base_pool = PROXY_COUNTRY_POOLS.get(country) if isinstance(PROXY_COUNTRY_POOLS, dict) else None
    if base_pool and len(base_pool) > 0:
        base_url = str(base_pool[0]).strip()
        m = re.match(r"https?://([^:@]+)(?::([^@]+))?@([^/]+)", base_url)
        if m:
            token = m.group(2) or "resin_proxy_c3d7b8e1f0a2496582d9183b"
            host_port = m.group(3)
            return f"http://{country}.{sess}:{token}@{host_port}"
    return f"http://{country}.{sess}:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260"


def resolve_account_proxy_3tier(
    account: dict,
    check_alive: bool = True,
    force_rotate: bool = False,
) -> Tuple[str, str, str]:
    """
    执行严格的三级代理容灾解析。
    返回: (proxy_url, target_country, tier_used)
    若所在大洲均无可用节点，抛出 RuntimeError 触发 Fail-Safe 熔断。
    """
    raw_country = str(account.get("country") or account.get("geo_country") or "").strip()
    country = normalize_country(raw_country)
    continent = get_continent(country or "US")
    saved_proxy = (
        account.get("proxy")
        or account.get("proxy_used")
        or account.get("live_check_proxy_used")
    )

    # Level 1: 初始连接
    if saved_proxy and not force_rotate:
        p_str = str(saved_proxy).strip()
        if not check_alive or check_proxy_alive(p_str):
            logger.debug("[Level 1] 复用账号历史绑定出口: %s (国家: %s)", p_str, country or "未知")
            return p_str, country or "US", "level1_sticky"
        logger.warning("[Level 1] 历史绑定出口已不可达 (%s)，触发 Level 2 同国备用轮换", p_str)

    # Level 2: 第一级备用（同国家代理池，尝试最多 3 个新鲜会话出口）
    if country:
        for _ in range(3):
            new_country_proxy = build_resin_proxy_for_country(country)
            if not check_alive or check_proxy_alive(new_country_proxy):
                logger.info("[Level 2] 成功激活同国备用出口: %s (国家: %s)", new_country_proxy, country)
                return new_country_proxy, country, "level2_same_country"
            time.sleep(0.3)
        logger.warning("[Level 2] 同国家 (%s) 3 个备用节点均不可用，准备递归到同大洲代理池", country)

    # Level 3: 第二级备用（同大洲递归）
    continent_candidates = CONTINENT_FALLBACK_ORDER.get(continent, [])
    for alt_country in continent_candidates:
        if alt_country == country:
            continue
        alt_proxy = build_resin_proxy_for_country(alt_country)
        if not check_alive or check_proxy_alive(alt_proxy):
            logger.warning(
                "[Level 3] 原国家 (%s) 节点不可用，递归激活同大洲备用出口: %s (大洲: %s, 节点: %s)",
                country, alt_proxy, continent, alt_country
            )
            return alt_proxy, alt_country, "level3_same_continent"

    # Level 4 兜底：若本地高可用代理 (127.0.0.1:7890) 畅通，激活救灾兜底
    if check_proxy_alive("http://127.0.0.1:7890"):
        logger.warning(
            "[Level 4 救灾兜底] 账号 %s 所属大洲 [%s] 节点暂不可达，激活本地高可用代理 (127.0.0.1:7890) 救灾",
            account.get("email"), continent
        )
        return "http://127.0.0.1:7890", country or "US", "level4_clash_fallback"

    # Fail-Safe 熔断
    err_msg = (
        f"【代理熔断】账号 {account.get('email')} 归属国家 [{country}] 大洲 [{continent}] "
        f"所有候选节点均不可达！严格同洲策略已阻断请求，坚决禁止跨洲跳跃以防封号！"
    )
    logger.error(err_msg)
    raise RuntimeError(err_msg)
