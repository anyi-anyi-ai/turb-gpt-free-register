# -*- coding: utf-8 -*-
"""
core/sms/base.py — 接码平台统一基类与数据契约
"""
from __future__ import annotations

import abc
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


class SmsProviderError(RuntimeError):
    """接码平台通用异常"""


class SmsNoNumbersError(SmsProviderError):
    """当前国家或服务暂无可用号码 (NO_NUMBERS)"""


class SmsNoBalanceError(SmsProviderError):
    """平台账户余额不足 (NO_BALANCE)"""


class SmsTimeoutError(SmsProviderError):
    """等待短信验证码超时"""


@dataclass
class SmsNumber:
    """接码号码资产元数据"""
    id: str                   # 平台激活 ID (如 vak 的 idNum)
    phone: str                # 标准格式手机号 (带+或纯数字)
    raw_phone: str            # 纯数字格式
    country: str              # 国家代码 (如 'ru', 'id', 'us')
    service: str              # 服务代码 (如 'dr', 'acz')
    price: float              # 购买单价 (USD/RUB)
    created_at: float = 0.0   # 获取时间戳

    def __post_init__(self):
        if not self.created_at:
            self.created_at = time.time()


@dataclass
class CountryPriceTier:
    """国家价格梯度条目"""
    country: str              # 国家代码
    country_name: str         # 国家全名
    price: float              # 单价
    count: int                # 可用库存
    service: str              # 对应的服务码


class BaseSmsProvider(abc.ABC):
    """接码平台抽象驱动基类"""

    @abc.abstractmethod
    def get_balance(self) -> float:
        """获取当前账户可用余额"""
        raise NotImplementedError

    @abc.abstractmethod
    def get_country_prices(
        self,
        service: str,
        max_price: Optional[float] = None,
        blacklist: Optional[List[str]] = None,
    ) -> List[CountryPriceTier]:
        """获取指定服务的所有国家报价与库存，过滤后按单价升序排列"""
        raise NotImplementedError

    @abc.abstractmethod
    def acquire_number(
        self,
        service: str,
        country: Optional[str] = None,
    ) -> SmsNumber:
        """下单购买租用一个手机号"""
        raise NotImplementedError

    @abc.abstractmethod
    def wait_for_sms_code(
        self,
        activation_id: str,
        timeout: int = 90,
        interval: int = 5,
    ) -> Optional[str]:
        """轮询等待短信验证码到达"""
        raise NotImplementedError

    @abc.abstractmethod
    def set_status(self, activation_id: str, status: str) -> bool:
        """
        设置号码状态：
        - 'bad': 报废/未收到码，释放并触发平台退款
        - 'end': 完成激活
        - 'send': 等待下一条短信
        """
        raise NotImplementedError
