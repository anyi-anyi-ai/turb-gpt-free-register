# -*- coding: utf-8 -*-
"""独立中转与多订阅容灾管理器 (Relay & Subscription Manager)

核心原则：
1. 严禁使用或暴露宿主机用户的个人代理端口（如 7890）；
2. 汇聚 88.88 原生跳板与用户提供的外部多订阅源，形成内部自包含的高可用中转池；
3. 为链式代理第一跳提供健康检查与平滑降级端点。
"""
from __future__ import annotations

import logging
import os
import socket
import urllib.parse
from pathlib import Path

logger = logging.getLogger("relay_manager")

# 专属于本项目的独立中转网关端口 (与用户个人软件完全隔离)
STANDALONE_RELAY_PORT = int(os.environ.get("RELAY_GATEWAY_PORT", "28890"))
STANDALONE_RELAY_HOST = os.environ.get("RELAY_GATEWAY_HOST", "127.0.0.1")

# 原生 88.88 中转跳板 (Resin 节点)
PRIMARY_RESIN_PROXY = os.environ.get(
    "UPSTREAM_RESIN_PROXY",
    "http://FAST_UPSTREAM:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260",
)

# 外部多订阅容灾配置列表 (支持逗号分隔多个订阅)
DEFAULT_SUBSCRIPTIONS = [
    "https://proxy.mcii.cc/sub/617238894e43a32f47361a5f271c4308"
]


def is_port_listening(host: str, port: int, timeout: float = 0.5) -> bool:
    """检测指定本地端口是否处于监听状态。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(timeout)
            return s.connect_ex((host, port)) == 0
    except Exception:
        return False


def get_active_upstream_relay() -> str:
    """获取当前生效的第一跳中转代理 URL（支持双轨自愈与优雅退守）。
    
    优先级：
    1. 优先检测专有独立中转网关 (127.0.0.1:28890)，若已就绪，享受多订阅储备 + 88.88 自动容灾；
    2. 若独立网关未启动，则优雅退守至原生 88.88 Resin 中转 (127.0.0.1:2260)；
    3. 严禁回退或访问宿主机用户的个人 7890 端口。
    """
    if is_port_listening(STANDALONE_RELAY_HOST, STANDALONE_RELAY_PORT, timeout=0.3):
        return f"http://{STANDALONE_RELAY_HOST}:{STANDALONE_RELAY_PORT}"
    
    # 优雅退守至原生 88.88
    return PRIMARY_RESIN_PROXY


def get_configured_subscriptions() -> list[str]:
    """获取配置的所有外部中转订阅列表。"""
    raw = os.environ.get("UPSTREAM_SUBSCRIPTION_URLS", "")
    if raw.strip():
        urls = [u.strip() for u in raw.split(",") if u.strip()]
        if urls:
            return urls
    return list(DEFAULT_SUBSCRIPTIONS)
