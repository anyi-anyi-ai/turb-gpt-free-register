# -*- coding: utf-8 -*-
"""
core/sms/session.py — 智能接码会话与双层重试状态机
核心业务逻辑：
  1. 动态生成国家低价梯队队列（最低价国 -> 次低价国 -> ...）；
  2. 内层循环：在当前最低价国家中连续取号换号（支持 5~10 次上限）；
  3. 未收到短信即时调用 setStatus(bad) 释放退费；
  4. 当前国连续尝试耗尽上限或售罄时，平滑晋升至次低价国家继续尝试。
"""
from __future__ import annotations

import logging
from typing import Generator, List, Optional

from core.sms.base import (
    CountryPriceTier,
    SmsNoBalanceError,
    SmsNoNumbersError,
    SmsNumber,
    SmsProviderError,
)
from core.sms.vak_provider import VakSmsProvider

logger = logging.getLogger("turb.sms.session")


class SmsAttempt:
    """单个号码激活尝试上下文"""

    def __init__(
        self,
        provider: VakSmsProvider,
        number: SmsNumber,
        country_name: str,
        attempt_index: int,
        country_index: int,
        max_attempts_in_country: int,
    ):
        self.provider = provider
        self.number = number
        self.activation_id = number.id
        self.phone = number.phone
        self.raw_phone = number.raw_phone
        self.country = number.country
        self.country_name = country_name
        self.price = number.price
        self.attempt_index = attempt_index
        self.country_index = country_index
        self.max_attempts_in_country = max_attempts_in_country

        self.is_success: bool = False
        self.is_bad: bool = False
        self.is_finished: bool = False

    def wait_for_code(self, timeout: int = 90, interval: int = 5) -> Optional[str]:
        """轮询等待短信验证码到达，超时自动返回 None"""
        code = self.provider.wait_for_sms_code(
            self.activation_id,
            timeout=timeout,
            interval=interval,
        )
        if code:
            self.report_success()
            return code
        else:
            self.report_bad(reason="sms_timeout")
            return None

    def report_success(self) -> None:
        """标记号码成功并锁定完成 (status=end)"""
        if self.is_finished:
            return
        self.provider.set_status(self.activation_id, "end")
        self.is_success = True
        self.is_finished = True
        logger.info(
            f"[SmsAttempt] 激活成功: {self.phone} (国别: {self.country}, 价格: ${self.price:.4f})"
        )

    def report_bad(self, reason: str = "failed") -> None:
        """标记号码作废并立即释放退费 (status=bad)"""
        if self.is_finished:
            return
        self.provider.set_status(self.activation_id, "bad")
        self.is_bad = True
        self.is_finished = True
        logger.info(
            f"[SmsAttempt] 作废退款: {self.phone} (原因: {reason}, 第 {self.attempt_index}/{self.max_attempts_in_country} 次)"
        )


class VakSmsSession:
    """双层重试接码会话管理器"""

    def __init__(
        self,
        provider: VakSmsProvider,
        service: str,
        max_retries_per_country: int = 5,
        max_country_hops: int = 5,
        max_price: Optional[float] = None,
        country_blacklist: Optional[List[str]] = None,
        default_timeout: int = 90,
    ):
        self.provider = provider
        self.service = service
        self.max_retries_per_country = max_retries_per_country
        self.max_country_hops = max_country_hops
        self.max_price = max_price
        self.country_blacklist = country_blacklist or []
        self.default_timeout = default_timeout

        self._active_attempt: Optional[SmsAttempt] = None
        self._closed: bool = False

    def __enter__(self) -> "VakSmsSession":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def close(self) -> None:
        """关闭会话，若有未收尾的号码，强制释放退费"""
        if self._closed:
            return
        if self._active_attempt and not self._active_attempt.is_finished:
            self._active_attempt.report_bad("session_closed")
        self._closed = True

    def iter_numbers(self) -> Generator[SmsAttempt, None, None]:
        """
        核心调度生成器：
        1. 遍历价格梯队队列（由最低价依次向次低价顺延）
        2. 在每个国家内部换号重试最多 max_retries_per_country 次
        3. 成功后直接返回；失败自动退款并换号
        """
        # 1. 获取按价格升序排序的国家队列
        tiers = self.provider.get_country_prices(
            service=self.service,
            max_price=self.max_price,
            blacklist=self.country_blacklist,
        )

        if not tiers:
            raise SmsNoNumbersError(
                f"服务 [{self.service}] 当前无可用低价国家（请检查库存、价格上限或网络）"
            )

        total_hops = min(len(tiers), self.max_country_hops)
        logger.info(
            f"[VakSmsSession] 已构建价格梯度队列 (共 {len(tiers)} 国可用, 允许顺延前 {total_hops} 国):"
        )
        for i, t in enumerate(tiers[:total_hops], start=1):
            logger.info(
                f"  [{i}] 国家: {t.country_name} ({t.country}), 单价: ${t.price:.4f}, 库存: {t.count} pc"
            )

        # 外层循环：遍历低价国家梯队
        for country_idx in range(total_hops):
            current_tier = tiers[country_idx]
            country_code = current_tier.country
            country_name = current_tier.country_name
            country_price = current_tier.price

            rank_title = "最低价国家" if country_idx == 0 else f"第 {country_idx + 1} 梯队次低价国家"
            logger.info(
                f"===> [梯队调度] 进入 {rank_title}: {country_name} ({country_code}), 单价: ${country_price:.4f}"
            )

            attempts_in_country = 0

            # 内层循环：在当前国家内换号重试
            while attempts_in_country < self.max_retries_per_country:
                attempts_in_country += 1

                try:
                    num_obj = self.provider.acquire_number(
                        service=self.service,
                        country=country_code,
                        max_price=self.max_price,
                    )
                except SmsNoNumbersError:
                    logger.warning(
                        f"[VakSmsSession] 国家 [{country_code}] 实时库存已耗尽，提前切换至次低价国家..."
                    )
                    break
                except SmsNoBalanceError:
                    raise
                except Exception as exc:
                    logger.warning(
                        f"[VakSmsSession] 国家 [{country_code}] 取号失败 ({exc})，尝试在该国换号..."
                    )
                    continue

                attempt = SmsAttempt(
                    provider=self.provider,
                    number=num_obj,
                    country_name=country_name,
                    attempt_index=attempts_in_country,
                    country_index=country_idx + 1,
                    max_attempts_in_country=self.max_retries_per_country,
                )
                self._active_attempt = attempt

                logger.info(
                    f"[VakSmsSession] 分配号码: {attempt.phone} "
                    f"({country_name}, 单价: ${country_price:.4f}, 尝试: {attempts_in_country}/{self.max_retries_per_country})"
                )

                # 将控制权交给调用方插件
                yield attempt

                # 调用方退出当前 step
                if attempt.is_success:
                    logger.info(
                        f"[VakSmsSession] 恭喜！在国家 [{country_code}] (单价: ${country_price:.4f}) 成功完成短信确权！"
                    )
                    return

                # 若插件未显式调用 report_success 或 report_bad，兜底设为 bad 退款
                if not attempt.is_finished:
                    attempt.report_bad("caller_unhandled")

                if attempts_in_country < self.max_retries_per_country:
                    logger.info(
                        f"[VakSmsSession] 国家 [{country_code}] 尚有换号配额 "
                        f"({attempts_in_country}/{self.max_retries_per_country})，准备在该国换取新号码..."
                    )
                else:
                    logger.warning(
                        f"[VakSmsSession] 国家 [{country_code}] 连续 {self.max_retries_per_country} 个号码均未能收码，"
                        f"已达单国配额上限，顺延至次低价国家！"
                    )

        raise RuntimeError(
            f"[VakSmsSession] 已尝试全部前 {total_hops} 个候选低价国家，均无法收到短信验证码！"
        )
