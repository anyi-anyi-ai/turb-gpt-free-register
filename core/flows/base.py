# -*- coding: utf-8 -*-
"""
core/flows/base.py — 注册流水线抽象基类（模板方法模式）。
规范所有平台的端到端注册生命周期：
  acquire_email -> build_driver -> execute -> persist -> push -> cleanup
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from core.flows.context import FlowContext, RegistrationResult
from core.flows.pool_helper import pick_unregistered_email_for_platform

logger = logging.getLogger("flow.base")


class BaseRegistrationFlow(ABC):
    """平台注册流水线模板基类。"""

    platform_name: str = "unknown"
    platform_table: str = ""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = db_path

    def pick_account(self, specific_email: Optional[str] = None) -> Dict[str, Any]:
        """从邮箱池中分配未在当前平台注册的可用账号。"""
        if not self.platform_table:
            raise NotImplementedError(f"未为平台 {self.platform_name} 指定 platform_table")
        return pick_unregistered_email_for_platform(
            platform_table=self.platform_table,
            specific_email=specific_email,
            db_path=self.db_path,
        )

    def build_driver(self, context: FlowContext) -> Any:
        """创建浏览器驱动实例。默认使用 CloakBrowser。"""
        from core.cloakbrowser_driver import build_cloak_driver
        driver, _ = build_cloak_driver(proxy=context.proxy, headless=context.headless)
        return driver

    @abstractmethod
    def execute(self, context: FlowContext) -> RegistrationResult:
        """执行具体平台的网页/协议注册步骤并提取凭据。由子类实现。"""
        pass

    def persist(self, context: FlowContext, result: RegistrationResult) -> bool:
        """持久化凭据至本地数据库。子类可重写。"""
        return True

    def push(self, context: FlowContext, result: RegistrationResult) -> bool:
        """推送到下游网关。子类可重写。"""
        return True

    def cleanup(self, context: FlowContext) -> None:
        """安全释放浏览器与网络会话。"""
        if context.driver:
            try:
                context.driver.quit()
            except Exception as e:
                logger.debug(f"[{self.platform_name}] 关闭 driver 时出现非阻塞异常: {e}")
            context.driver = None

    def run(
        self,
        account_info: Optional[Dict[str, Any]] = None,
        proxy: Optional[str] = None,
        headless: bool = True,
        **kwargs: Any,
    ) -> RegistrationResult:
        """
        模板方法：驱动单账号注册的标准执行流水线。
        """
        if account_info is None:
            account_info = self.pick_account(specific_email=kwargs.get("specific_email"))

        email = account_info.get("email", "").strip().lower()
        context = FlowContext(
            platform=self.platform_name,
            email=email,
            proxy=proxy,
            headless=headless,
            db_path=self.db_path,
            extra_payload=account_info.get("payload", {}),
            metadata=kwargs,
            start_time=time.time(),
        )

        logger.info(f"[{self.platform_name}] 启动注册流水线: {email}")

        try:
            context.driver = self.build_driver(context)
            result = self.execute(context)

            if result.ok:
                self.persist(context, result)
                if kwargs.get("auto_push", True):
                    self.push(context, result)

            return result

        except Exception as exc:
            logger.error(f"[{self.platform_name}] 注册流程发生异常 ({email}): {exc}", exc_info=True)
            return RegistrationResult(
                ok=False,
                email=email,
                platform=self.platform_name,
                error=str(exc),
            )

        finally:
            self.cleanup(context)
