# -*- coding: utf-8 -*-
"""代理商采集驱动抽象基类与接口契约。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Callable

from core.proxy_harvester.models import ProxyAccountRecord


class BaseProxyProvider(ABC):
    """所有代理商驱动必须实现的抽象基类。"""

    provider_name: str = "base"

    def __init__(
        self,
        headless: bool = True,
        upstream_proxy: str | None = None,
        timeout_sec: int = 75,
    ):
        self.headless = headless
        self.upstream_proxy = upstream_proxy
        self.timeout_sec = timeout_sec

    @abstractmethod
    def harvest_account(
        self,
        email: str,
        activation_link_getter: Callable[[], str | None],
        email_source: str = "outlook",
        batch_id: str = "",
        verification_code_getter: Callable[[], str | None] | None = None,
    ) -> ProxyAccountRecord | None:
        """执行单账号注册、过盾、邮箱激活/验证码确权并导出该账号名下的代理列表。"""
        raise NotImplementedError
