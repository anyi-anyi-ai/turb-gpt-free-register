# -*- coding: utf-8 -*-
"""
core/kits — 原子积木组件包 (Lego Kits Bundle)
提供注册流水线开箱即用的 6 大通用原子能力：
  - EmailKit: 邮箱分配与激活收信
  - BrowserKit: 浏览器多核生命周期与指纹驱动
  - HumanKit: 拟人击键动力学与仿生轨迹
  - CaptchaKit: Turnstile 穿透与人机协同通关
  - AssetKit: 凭据落盘入库与下游网关同步
  - SmsKit: 智能接码、最低价比对、单国多号轮换与跨国顺延退费
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from core.kits.email_kit import EmailKit
from core.kits.browser_kit import BrowserKit
from core.kits.human_kit import HumanKit
from core.kits.captcha_kit import CaptchaKit
from core.kits.asset_kit import AssetKit
from core.kits.sms_kit import SmsKit


@dataclass
class KitBundle:
    """原子积木全量注入容器。"""
    email: EmailKit
    browser: BrowserKit
    human: HumanKit
    captcha: CaptchaKit
    asset: AssetKit
    sms: SmsKit

    @classmethod
    def create(cls, db_path: Optional[Path | str] = None) -> "KitBundle":
        return cls(
            email=EmailKit(db_path=db_path),
            browser=BrowserKit(),
            human=HumanKit(),
            captcha=CaptchaKit(),
            asset=AssetKit(db_path=db_path),
            sms=SmsKit(),
        )

    @classmethod
    def create_dual_session(
        cls,
        target_country: str = "US",
        proxy: Optional[str] = None,
        browser_type: str = "camoufox",
    ) -> Any:
        """快速构建轻重双模统一会话 (DualSession)。"""
        from core.flows.dual_session import DualSession
        return DualSession(
            target_country=target_country,
            proxy=proxy,
            browser_type=browser_type,
        )


__all__ = [
    "EmailKit",
    "BrowserKit",
    "HumanKit",
    "CaptchaKit",
    "AssetKit",
    "SmsKit",
    "KitBundle",
]
