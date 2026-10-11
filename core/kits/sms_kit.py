# -*- coding: utf-8 -*-
"""
core/kits/sms_kit.py — 通用接码原子积木 (SmsKit)
作为 Turb 注册流水线的第 6 大通用积木，直接注入 KitBundle。
支持在任何网站注册插件中调用：
  - kits.sms.session(service="...", max_retries_per_country=5)
  - 自动全国家最低价比价、单国 5~10 次换号重试、次低价国家顺延与自动退费熔断
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from core.sms.base import CountryPriceTier, SmsNumber
from core.sms.session import VakSmsSession
from core.sms.vak_provider import VakSmsProvider

logger = logging.getLogger("turb.kits.sms")


class SmsKit:
    """短信接码原子积木组件"""

    def __init__(
        self,
        provider: Optional[VakSmsProvider] = None,
        default_max_retries: int = 5,
        default_country_hops: int = 5,
        default_timeout: int = 90,
    ):
        self.provider = provider or VakSmsProvider()
        self.default_max_retries = int(
            os.getenv("VAK_MAX_RETRIES_PER_COUNTRY") or default_max_retries
        )
        self.default_country_hops = int(
            os.getenv("VAK_MAX_COUNTRY_HOPS") or default_country_hops
        )
        self.default_timeout = int(
            os.getenv("VAK_SMS_TIMEOUT") or default_timeout
        )

    def session(
        self,
        service: str,
        max_retries_per_country: Optional[int] = None,
        max_country_hops: Optional[int] = None,
        max_price: Optional[float] = None,
        country_blacklist: Optional[List[str]] = None,
        timeout: Optional[int] = None,
    ) -> VakSmsSession:
        """
        开启一个高阶智能接码会话（推荐插件使用）：
        - 自动按最低价排序全国家
        - 在最低价国家尝试最多 max_retries_per_country 次（默认 5，可配 5~10）
        - 失败自动 setStatus(bad) 退款并换号
        - 超过次数或售罄后平滑顺延至次低价国家

        用法范例：
            with kits.sms.session(service="claude", max_retries_per_country=5) as sms:
                for attempt in sms.iter_numbers():
                    kits.human.type_text(page, "#phone", attempt.phone)
                    kits.human.click_element(page, "#send")
                    code = attempt.wait_for_code(timeout=90)
                    if code:
                        kits.human.type_text(page, "#code", code)
                        break
        """
        # 优先入参，次查环境变量
        retries = max_retries_per_country or self.default_max_retries
        hops = max_country_hops or self.default_country_hops
        t_out = timeout or self.default_timeout

        env_max_price = os.getenv("VAK_MAX_PRICE")
        price_limit = max_price if max_price is not None else (
            float(env_max_price) if env_max_price else None
        )

        blacklist = country_blacklist
        if blacklist is None:
            raw_bl = os.getenv("VAK_COUNTRY_BLACKLIST", "")
            blacklist = [x.strip() for x in raw_bl.split(",") if x.strip()]

        return VakSmsSession(
            provider=self.provider,
            service=service,
            max_retries_per_country=retries,
            max_country_hops=hops,
            max_price=price_limit,
            country_blacklist=blacklist,
            default_timeout=t_out,
        )

    def acquire_number(
        self,
        service: str,
        country: Optional[str] = None,
        max_price: Optional[float] = None,
    ) -> Tuple[str, str]:
        """
        单次底层取号（兼容轻量调用）：
        返回: (activation_id, phone)
        """
        num: SmsNumber = self.provider.acquire_number(
            service=service,
            country=country,
            max_price=max_price,
        )
        return num.id, num.phone

    def wait_code(self, activation_id: str, timeout: int = 90) -> Optional[str]:
        """轮询接收短信验证码"""
        return self.provider.wait_for_sms_code(activation_id, timeout=timeout)

    def release_number(self, activation_id: str, reason: str = "bad") -> bool:
        """
        释放号码：
        - reason='bad': 报废并退款
        - reason='end': 标记完成
        """
        status = "end" if reason in ("end", "success", "ok") else "bad"
        return self.provider.set_status(activation_id, status=status)

    def get_balance(self) -> float:
        """获取接码平台账户余额"""
        return self.provider.get_balance()

    def get_price_ranking(
        self,
        service: str,
        max_price: Optional[float] = None,
        top: int = 10,
    ) -> List[Dict[str, Any]]:
        """获取指定服务的国家最低价排行榜"""
        tiers: List[CountryPriceTier] = self.provider.get_country_prices(
            service=service,
            max_price=max_price,
        )
        return [
            {
                "country": t.country,
                "country_name": t.country_name,
                "price": t.price,
                "count": t.count,
                "service": t.service,
            }
            for t in tiers[:top]
        ]
