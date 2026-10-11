# -*- coding: utf-8 -*-
"""Proxy Harvester: 100GB / 1000 节点纯净代理自动化获取子系统。"""
from __future__ import annotations

from core.proxy_harvester.base import BaseProxyProvider
from core.proxy_harvester.models import ProxyAccountRecord, HarvestBatch
from core.proxy_harvester.email_dispatcher import CascadingEmailDispatcher
from core.proxy_harvester.resin_egress import ResinEgressManager
from core.proxy_harvester.webshare_provider import WebshareHarvester
from core.proxy_harvester.registry import (
    create_provider_instance,
    get_available_providers,
    register_provider,
)
from core.proxy_harvester.controller import HarvesterController

__all__ = [
    "BaseProxyProvider",
    "ProxyAccountRecord",
    "HarvestBatch",
    "CascadingEmailDispatcher",
    "ResinEgressManager",
    "WebshareHarvester",
    "create_provider_instance",
    "get_available_providers",
    "register_provider",
    "HarvesterController",
]
