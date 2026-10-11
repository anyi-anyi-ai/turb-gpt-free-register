# -*- coding: utf-8 -*-
"""代理商驱动注册表与轮询调度工厂。"""
from __future__ import annotations

import logging
from typing import Type

from core.proxy_harvester.base import BaseProxyProvider
from core.proxy_harvester.webshare_provider import WebshareHarvester
from core.proxy_harvester.proxyscrape_provider import ProxyScrapeHarvester
from core.proxy_harvester.plainproxies_provider import PlainProxiesHarvester

logger = logging.getLogger("harvester.registry")

# 驱动注册表
_PROVIDER_REGISTRY: dict[str, Type[BaseProxyProvider]] = {
    "webshare": WebshareHarvester,
    "plainproxies": PlainProxiesHarvester,
    "proxyscrape": ProxyScrapeHarvester,
}


def register_provider(name: str, provider_cls: Type[BaseProxyProvider]) -> None:
    """注册新的第三方代理商驱动。"""
    _PROVIDER_REGISTRY[name.lower()] = provider_cls


def get_available_providers() -> list[str]:
    """获取所有已启用的代理商驱动列表。"""
    return list(_PROVIDER_REGISTRY.keys())


def create_provider_instance(
    provider_name: str,
    headless: bool = True,
    upstream_proxy: str | None = None,
    account_index: int = 1,
) -> BaseProxyProvider:
    """根据代理商名称或轮询策略实例化驱动。
    
    支持:
      - 具体名称: 'webshare', 'plainproxies'
      - 轮询策略: 'auto' (根据 account_index 在注册表中依次循环分配)
    """
    keys = list(_PROVIDER_REGISTRY.keys())
    if not keys:
        raise RuntimeError("未注册任何可用的代理商驱动")

    chosen_name = provider_name.lower().strip()
    if chosen_name == "auto":
        # 轮询循环选择
        chosen_name = keys[(account_index - 1) % len(keys)]
        logger.info(f"🔄 [多代理商轮询] 第 {account_index} 个账号调度至驱动: {chosen_name}")

    cls = _PROVIDER_REGISTRY.get(chosen_name)
    if not cls:
        logger.warning(f"⚠️ 未找到代理商驱动 [{chosen_name}]，自动回退至默认 [webshare]")
        cls = WebshareHarvester

    return cls(headless=headless, upstream_proxy=upstream_proxy)
