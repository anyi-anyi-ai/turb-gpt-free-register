# -*- coding: utf-8 -*-
"""
core/plugins/base.py — 网站注册插件标准规范抽象基类 (BaseSitePlugin)
规范所有网站插件的元数据契约与统一生命周期：
  pick_account -> launch_driver -> execute (由插件编写) -> persist -> push -> cleanup
"""
from __future__ import annotations

import logging
import secrets
import string
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, Optional

from core.flows.context import FlowContext, RegistrationResult
from core.kits import KitBundle

logger = logging.getLogger("turb.plugin.base")


def _generate_default_password() -> str:
    """生成兼顾大小写、数字与特殊符号的高强度安全密码 (CSPRNG)。"""
    chars = string.ascii_letters + string.digits + "!@#$%^&*"
    core = "".join(secrets.choice(chars) for _ in range(12))
    return f"Turb{core}#2026!"


class BaseSitePlugin(ABC):
    """
    所有网站注册插件的统一基类。
    新增网站注册只需继承此类，实现 `execute` 核心交互步骤，其余生命周期全部自动化完成。
    """

    id: str = "unknown"
    name: str = "Unknown Site"
    description: str = ""
    target_url: str = ""
    table_name: str = ""
    browser_type: str = "cloak"  # "cloak" | "patchright"
    requires_human: bool = False  # 是否包含图形拼图等需要人机极速协同的步骤
    default_headless: bool = True

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = db_path
        self.kits = KitBundle.create(db_path=self.db_path)

    def generate_password(self) -> str:
        """生成符合目标平台要求的密码，子类可按需覆盖。"""
        return _generate_default_password()

    def pick_account(self, specific_email: Optional[str] = None) -> Dict[str, Any]:
        """从邮箱池中分配未在当前平台注册的可用账号。"""
        if not self.table_name:
            raise NotImplementedError(f"未为插件 {self.id} 指定 table_name")
        return self.kits.email.pick_account(
            platform_table=self.table_name,
            specific_email=specific_email,
        )

    @abstractmethod
    def execute(self, ctx: FlowContext, kits: KitBundle) -> RegistrationResult:
        """
        核心方法：执行目标网页的具体注册表单交互、打码及凭证抽取。
        由各网站插件具体实现（通常仅需 15~30 行代码）。
        """
        pass

    def persist(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> bool:
        """将注册成功的凭证持久化至 SQLite 数据库。默认实现直接调用 AssetKit。"""
        if not self.table_name:
            return True
        return kits.asset.save_account(
            table_name=self.table_name,
            email=result.email,
            password=ctx.password,
            api_key=result.access_token or result.metadata.get("api_key", ""),
            session_token=result.refresh_token or result.metadata.get("session_token", ""),
            refresh_token=result.refresh_token,
            cookies=result.cookies,
            credits=result.credits,
            status=result.status,
            synced_to_gateway=result.synced_to_gateway,
            metadata=result.metadata,
        )

    def push(self, ctx: FlowContext, result: RegistrationResult, kits: KitBundle) -> bool:
        """推送到下游网关。子类可根据需要重写以对接专用网关。"""
        return True

    def run(
        self,
        specific_email: Optional[str] = None,
        proxy: Optional[str] = None,
        headless: Optional[bool] = None,
        auto_push: bool = True,
        **kwargs: Any,
    ) -> RegistrationResult:
        """
        模板方法：驱动单账号注册的标准端到端生命周期。
        """
        account_info = self.pick_account(specific_email=specific_email)
        email = account_info.get("email", "").strip().lower()
        password = account_info.get("payload", {}).get("password") or self.generate_password()

        is_headless = self.default_headless if headless is None else headless
        # 如果该平台需要人机拼图交互，强制可视化窗口
        if self.requires_human and is_headless:
            logger.info(f"[{self.id}] 该平台声明了 requires_human=True，自动切换为可视化有头窗口以支持人机协同。")
            is_headless = False

        # 0. 前置高粘性节点池接入与门禁审计 (Pre-flight Sticky Proxy Gate)
        effective_proxy = proxy
        if not effective_proxy:
            try:
                from config.proxy import pick_proxy
                from core.proxy_quality import log_and_audit_proxy_gate
                from core.ip_quota_manager import ip_quota_manager

                target_country = getattr(self, "target_country", None)
                for _try in range(5):
                    allocated = pick_proxy(country=target_country, for_registration=True)
                    if not allocated:
                        break
                    is_clean = log_and_audit_proxy_gate(allocated)
                    if is_clean:
                        effective_proxy = allocated
                        logger.info(f"[{self.id}] [前置代理池] 成功接入纯净粘性住宅节点: {effective_proxy}")
                        break
                    else:
                        logger.warning(f"[{self.id}] [前置门禁拦截] 节点 {allocated} 未通过住宅纯净度审计，轮换下一个...")
                        ip_quota_manager.record_proxy_failure(allocated, reason="datacenter_detected", country=target_country)
                if not effective_proxy:
                    logger.error(f"[{self.id}] [前置代理池] 阻断: 未能分配到纯净粘性住宅节点，触发门禁熔断，禁止使用宿主直连！")
                    return RegistrationResult(
                        success=False,
                        platform=self.id,
                        email=email,
                        error="residential_proxy_required: 未能分配到合规的纯净粘性住宅节点，门禁已熔断阻断",
                    )
            except Exception as proxy_err:
                logger.error(f"[{self.id}] [前置代理池] 接入粘性节点池异常: {proxy_err}")
        else:
            try:
                from core.proxy_quality import log_and_audit_proxy_gate
                log_and_audit_proxy_gate(effective_proxy)
            except Exception:
                pass

        ctx = FlowContext(
            platform=self.id,
            email=email,
            password=password,
            proxy=effective_proxy,
            headless=is_headless,
            db_path=self.db_path,
            extra_payload=account_info.get("payload", {}),
            metadata=kwargs,
            start_time=time.time(),
        )

        logger.info(f"[{self.id}] >>> 启动标准注册流水线: {email} (Proxy: {effective_proxy or 'Direct'}, Headless: {is_headless})")
        driver = None

        try:
            # 1. 启动浏览器 (appium/模拟器或自定义驱动由具体插件自身托管)
            if (
                self.browser_type
                and self.browser_type.lower() not in ("appium", "custom", "none", "external")
                and not getattr(self, "manage_driver_internally", False)
            ):
                driver = self.kits.browser.launch(
                    browser_type=self.browser_type,
                    proxy=effective_proxy,
                    headless=is_headless,
                    account_email=email,
                )
                ctx.driver = driver

            # 2. 执行核心插件交互
            result = self.execute(ctx, self.kits)

            # 3. 结果处理
            if result.ok:
                logger.info(f"[{self.id}] 注册执行成功: {email}")
                self.persist(ctx, result, self.kits)
                if auto_push:
                    try:
                        self.push(ctx, result, self.kits)
                    except Exception as push_err:
                        logger.warning(f"[{self.id}] 推送下游网关时出现非阻塞异常: {push_err}")
            else:
                logger.warning(f"[{self.id}] 注册执行未达预期: {result.error}")
                if effective_proxy:
                    try:
                        from core.ip_quota_manager import ip_quota_manager
                        target_country = getattr(self, "target_country", None)
                        ip_quota_manager.record_proxy_failure(effective_proxy, reason="reg_failed", country=target_country)
                    except Exception:
                        pass

            return result

        except Exception as exc:
            logger.error(f"[{self.id}] 注册流水线异常中断 ({email}): {exc}", exc_info=True)
            return RegistrationResult(
                ok=False,
                email=email,
                platform=self.id,
                error=str(exc),
            )

        finally:
            # 4. 安全清理
            if driver:
                self.kits.browser.close(driver)
                ctx.driver = None
