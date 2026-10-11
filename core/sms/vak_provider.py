# -*- coding: utf-8 -*-
"""
core/sms/vak_provider.py — VAK-SMS 接码平台驱动实现
特性：
  - 基于 curl_cffi 模拟 Chrome120，规避 Cloudflare 403 盾
  - 自动域名故障转移 (vak-sms.com -> moresms.net -> vaksms.ru)
  - 智能平台别名解析 (openai/chatgpt -> dr, claude -> acz 等)
  - 多国并发报价比对与智能低价排序
  - atexit 异常崩溃退款守卫，杜绝暗扣
"""
from __future__ import annotations

import atexit
import concurrent.futures
import json
import logging
import os
import time
from typing import Any, Dict, List, Optional, Tuple

from curl_cffi.requests import Session as CurlSession

from core.sms.base import (
    BaseSmsProvider,
    CountryPriceTier,
    SmsNoBalanceError,
    SmsNoNumbersError,
    SmsNumber,
    SmsProviderError,
    SmsTimeoutError,
)

logger = logging.getLogger("turb.sms.vak")

# 全局活跃取号记录：用于进程意外退出或异常时自动触发 setStatus(bad) 释放退款
_ACTIVE_ALLOCATIONS: Dict[str, Dict[str, Any]] = {}

# 常用平台名称与 vak-sms 官方服务码映射
SERVICE_ALIASES: Dict[str, str] = {
    # 大模型与 AI 平台
    "openai": "dr",
    "chatgpt": "dr",
    "codex": "dr",
    "claude": "acz",
    "anthropic": "acz",
    "grok": "tw",
    "xai": "tw",
    "elevenlabs": "elevenlabs",
    # 国际主流通讯与社交
    "google": "go",
    "gmail": "go",
    "telegram": "tg",
    "tg": "tg",
    "whatsapp": "wa",
    "wa": "wa",
    "microsoft": "mm",
    "outlook": "mm",
    "hotmail": "mm",
    "twitter": "tw",
    "x": "tw",
    "discord": "ds",
    "facebook": "fb",
    "instagram": "ig",
    "tiktok": "lf",
    "apple": "wx",
    "amazon": "am",
    "line": "me",
    "viber": "vi",
    "snapchat": "fu",
    "signal": "bw",
    # 金融与支付
    "paypal": "ts",
    "payoneer": "nc",
    "stripe": "nu",
    "coinbase": "re",
    "binance": "aon",
    "bybit": "abn",
    "revolut": "ij",
    "wise": "bo",
    # 娱乐与游戏
    "steam": "mt",
    "netflix": "nf",
    "spotify": "alj",
    "uber": "ub",
    "tinder": "oi",
    "blizzard": "bz",
    "roblox": "aiw",
}

# 常见国家代码与展示名
COUNTRY_NAMES: Dict[str, str] = {
    "ru": "Russia",
    "us": "United States",
    "us_phys": "United States Physical",
    "gb": "United Kingdom",
    "id": "Indonesia",
    "ph": "Philippines",
    "vn": "Vietnam",
    "th": "Thailand",
    "my": "Malaysia",
    "in": "India",
    "br": "Brazil",
    "fr": "France",
    "de": "Germany",
    "es": "Spain",
    "nl": "Netherlands",
    "pl": "Poland",
    "ca": "Canada",
    "au": "Australia",
    "kz": "Kazakhstan",
    "ua": "Ukraine",
    "cl": "Chile",
    "ar": "Argentina",
    "co": "Colombia",
    "mx": "Mexico",
    "za": "South Africa",
    "eg": "Egypt",
    "ro": "Romania",
    "cz": "Czechia",
    "se": "Sweden",
    "pt": "Portugal",
    "ee": "Estonia",
    "lt": "Lithuania",
    "lv": "Latvia",
    "be": "Belgium",
    "at": "Austria",
    "fi": "Finland",
    "gr": "Greece",
    "ie": "Ireland",
    "it": "Italy",
}

# 推荐默认优先轮询的优质候选国家列表（按广度与通常价格）
POPULAR_COUNTRIES = [
    "id", "ph", "vn", "th", "my", "cl", "ar", "co", "mx", "br",
    "ru", "kz", "ua", "za", "eg", "ro", "pl", "pt", "es", "fr",
    "de", "nl", "gb", "us", "ca", "au", "ee", "lv", "lt", "cz",
]


class VakSmsProvider(BaseSmsProvider):
    """VAK-SMS 平台核心驱动"""

    DEFAULT_BASE_URLS = [
        "https://vak-sms.com",
        "https://moresms.net",
        "https://vaksms.ru",
    ]

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: int = 30,
        proxy: Optional[str] = None,
    ):
        # 优先入参，次查 VAK_SMS_API_KEY，再查全局 SMS_API_KEY
        self.api_key = str(
            api_key
            or os.getenv("VAK_SMS_API_KEY")
            or os.getenv("SMS_API_KEY")
            or ""
        ).strip()
        self.preferred_base_url = (base_url or os.getenv("VAK_SMS_BASE_URL") or "").strip()
        self.timeout = timeout
        self.proxy = proxy

        # 价格与余量本地缓存: key -> (timestamp, list[CountryPriceTier])
        self._price_cache: Dict[str, Tuple[float, List[CountryPriceTier]]] = {}
        self._cache_ttl = 90.0  # 90 秒缓存，兼顾准确性与防限流

    def _http_session(self) -> CurlSession:
        """创建抗指纹拦截的 curl_cffi Session"""
        session = CurlSession(impersonate="chrome120")
        session.timeout = self.timeout
        if self.proxy:
            session.proxies = {"http": self.proxy, "https": self.proxy}
        return session

    def _send_request(self, uri: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """向 VAK-SMS 发起带有多镜像容灾的 HTTP GET 请求"""
        if not self.api_key:
            raise SmsProviderError("VAK-SMS 缺少 API Key，请在 .env 中配置 VAK_SMS_API_KEY")

        req_params = dict(params or {})
        req_params["apiKey"] = self.api_key

        urls = [self.preferred_base_url] if self.preferred_base_url else self.DEFAULT_BASE_URLS
        last_error = None

        with self._http_session() as s:
            for base in urls:
                if not base:
                    continue
                full_url = f"{base.rstrip('/')}/{uri.lstrip('/')}"
                try:
                    resp = s.get(full_url, params=req_params)
                    if resp.status_code == 200:
                        try:
                            data = resp.json()
                            if isinstance(data, dict):
                                error = data.get("error")
                                if error:
                                    self._handle_api_error(error)
                                return data
                            return {"data": data}
                        except json.JSONDecodeError:
                            pass
                    elif resp.status_code in (400, 401, 403):
                        try:
                            err_json = resp.json()
                            msg = err_json.get("message") or err_json.get("code") or resp.text
                            if "UserNotFound" in str(msg) or "apiKeyNotFound" in str(msg):
                                raise SmsProviderError(f"VAK-SMS API Key 无效或未找到账户: {msg}")
                        except Exception:
                            pass
                        raise SmsProviderError(f"VAK-SMS 请求返回 HTTP {resp.status_code}: {resp.text[:200]}")
                except (SmsProviderError, SmsNoBalanceError, SmsNoNumbersError):
                    raise
                except Exception as exc:
                    last_error = exc
                    logger.debug(f"[VAK-SMS] 镜像 {base} 访问失败 ({exc})，尝试备用域名...")
                    continue

        raise SmsProviderError(f"VAK-SMS 所有可用域名镜像均无法连接: {last_error}")

    def _handle_api_error(self, error_str: str) -> None:
        """统一识别并处理 VAK 错误代码"""
        error_lower = str(error_str).lower()
        if "nomoney" in error_lower or "nobalance" in error_lower or "notenoughbalance" in error_lower:
            raise SmsNoBalanceError("VAK-SMS 账户余额不足，请充值后重试")
        if "nonumber" in error_lower or "nonumbers" in error_lower:
            raise SmsNoNumbersError("VAK-SMS 暂无可用号码")
        if "apikeynotfound" in error_lower or "badkey" in error_lower:
            raise SmsProviderError("VAK-SMS API Key 错误或未生效")
        if "badservice" in error_lower:
            raise SmsProviderError(f"VAK-SMS 不支持的服务代码: {error_str}")
        if "nocountry" in error_lower or "badcountry" in error_lower:
            raise SmsProviderError(f"VAK-SMS 不支持的国家代码: {error_str}")
        raise SmsProviderError(f"VAK-SMS 接口返回错误: {error_str}")

    @classmethod
    def resolve_service(cls, service_input: str) -> str:
        """将用户友好的别名（如 openai/claude）解析为官方代码（dr/acz）"""
        clean = str(service_input or "").strip().lower()
        return SERVICE_ALIASES.get(clean, clean)

    def get_balance(self) -> float:
        """获取账户余额 (USD/RUB)"""
        res = self._send_request("/api/getBalance")
        balance = res.get("balance", 0.0)
        try:
            return float(balance)
        except (ValueError, TypeError):
            return 0.0

    def _fetch_single_country_price(self, service: str, country: str) -> Optional[CountryPriceTier]:
        """查询单个国家针对指定服务的库存与价格"""
        try:
            res = self._send_request(
                "/api/getCountNumber",
                {"service": service, "country": country, "price": 1},
            )
            count = res.get(service, 0)
            price = res.get("price", 0.0)
            if count is not None and count > 0:
                return CountryPriceTier(
                    country=country,
                    country_name=COUNTRY_NAMES.get(country, country.upper()),
                    price=float(price),
                    count=int(count),
                    service=service,
                )
        except Exception:
            pass
        return None

    def get_country_prices(
        self,
        service: str,
        max_price: Optional[float] = None,
        blacklist: Optional[List[str]] = None,
        top_countries: Optional[List[str]] = None,
    ) -> List[CountryPriceTier]:
        """
        并发检索所有候选国家的可用库存与价格，
        排除 0 库存、超价和黑名单国家，按单价升序（Price ASC）排列。
        """
        service_code = self.resolve_service(service)
        blacklist_set = {str(x).strip().lower() for x in (blacklist or []) if str(x).strip()}

        cache_key = f"{service_code}"
        now = time.time()
        if cache_key in self._price_cache:
            ts, cached_tiers = self._price_cache[cache_key]
            if now - ts < self._cache_ttl:
                return [
                    t for t in cached_tiers
                    if t.country not in blacklist_set and (max_price is None or t.price <= max_price)
                ]

        candidates = top_countries or POPULAR_COUNTRIES
        tiers: List[CountryPriceTier] = []

        # 并发查询以确保在 1~2 秒内拉取全量 30+ 国家报价
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            future_to_c = {
                executor.submit(self._fetch_single_country_price, service_code, c): c
                for c in candidates
                if c not in blacklist_set
            }
            for future in concurrent.futures.as_completed(future_to_c):
                res = future.result()
                if res and res.count > 0:
                    if max_price is None or res.price <= max_price:
                        tiers.append(res)

        # 排序策略：单价由低到高（Price ASC），若价格相同则按库存由大到小（Count DESC）
        tiers.sort(key=lambda x: (x.price, -x.count))

        self._price_cache[cache_key] = (now, tiers)
        return tiers

    def acquire_number(
        self,
        service: str,
        country: Optional[str] = None,
        max_price: Optional[float] = None,
    ) -> SmsNumber:
        """
        在指定或最低价国家下单取号。
        若未指定 country，自动选择当前价格最低且有库存的国家。
        """
        service_code = self.resolve_service(service)

        target_country = country
        target_price = 0.0

        if not target_country:
            tiers = self.get_country_prices(service_code, max_price=max_price)
            if not tiers:
                raise SmsNoNumbersError(f"服务 [{service}] 当前所有候选国家均无可用库存或超出价格上限")
            target_country = tiers[0].country
            target_price = tiers[0].price

        params = {
            "service": service_code,
            "country": target_country,
            "softId": "1019",
        }

        res = self._send_request("/api/getNumber/", params)
        tel = str(res.get("tel") or "").strip()
        id_num = str(res.get("idNum") or "").strip()

        if not tel or not id_num:
            raise SmsProviderError(f"VAK-SMS 取号响应缺少 tel 或 idNum: {res}")

        raw_phone = "".join(ch for ch in tel if ch.isdigit())
        phone = f"+{raw_phone}"

        item = SmsNumber(
            id=id_num,
            phone=phone,
            raw_phone=raw_phone,
            country=target_country,
            service=service_code,
            price=target_price,
        )

        # 登记到活跃号码表，用于进程级退款守护
        _ACTIVE_ALLOCATIONS[id_num] = {
            "provider": self,
            "phone": phone,
            "created_at": time.time(),
        }

        logger.info(f"[VAK-SMS] 成功获取号码: {phone} (国别: {target_country}, ID: {id_num})")
        return item

    def wait_for_sms_code(
        self,
        activation_id: str,
        timeout: int = 90,
        interval: int = 5,
    ) -> Optional[str]:
        """轮询接口直到收到短信验证码或超时"""
        activation_id = str(activation_id).strip()
        if not activation_id:
            raise SmsProviderError("wait_for_sms_code 缺少 activation_id")

        start = time.time()
        logger.info(f"[VAK-SMS] 开始等待验证码 (ID: {activation_id}, 超时: {timeout}s)...")

        # 触发等待通知状态
        try:
            self.set_status(activation_id, "send")
        except Exception:
            pass

        while time.time() - start < timeout:
            time.sleep(interval)
            try:
                res = self._send_request("/api/getSmsCode/", {"idNum": activation_id})
                code = res.get("smsCode")
                if code:
                    if isinstance(code, list) and code:
                        code = code[-1]
                    code_str = str(code).strip()
                    if code_str and code_str.lower() != "none" and code_str.lower() != "null":
                        logger.info(f"[VAK-SMS] 成功收到验证码: {code_str} (ID: {activation_id})")
                        return code_str
            except SmsProviderError:
                pass

        logger.warning(f"[VAK-SMS] 等待验证码超时 {timeout}s (ID: {activation_id})")
        return None

    def set_status(self, activation_id: str, status: str) -> bool:
        """
        设置号码状态并维护退款记录：
        - 'bad': 释放号码退款
        - 'end': 完成激活
        - 'send': 接收新短信
        """
        activation_id = str(activation_id).strip()
        status_val = str(status).strip().lower()
        if status_val not in ("bad", "end", "send"):
            raise SmsProviderError(f"不支持的状态值: {status}")

        try:
            res = self._send_request("/api/setStatus", {"idNum": activation_id, "status": status_val})
            ret_status = res.get("status")
            success = ret_status == "ready" or ret_status == "update" or bool(res)
            if success and status_val in ("bad", "end"):
                _ACTIVE_ALLOCATIONS.pop(activation_id, None)
            return success
        except Exception as exc:
            logger.debug(f"[VAK-SMS] setStatus 出现异常 (ID: {activation_id}, status: {status_val}): {exc}")
            if status_val in ("bad", "end"):
                _ACTIVE_ALLOCATIONS.pop(activation_id, None)
            return False


# ============================================================
# 进程退出自动退款守护 (Atexit Guard)
# ============================================================
@atexit.register
def _cleanup_unreleased_numbers():
    """当主进程退出或异常终止时，自动扫描未完成号码并发起 bad 退款"""
    if not _ACTIVE_ALLOCATIONS:
        return
    logger.info(f"[VAK-SMS] 进程退出钩子触发，正在清理并释放 {len(_ACTIVE_ALLOCATIONS)} 个未完成号码...")
    for id_num, meta in list(_ACTIVE_ALLOCATIONS.items()):
        try:
            provider: VakSmsProvider = meta.get("provider")
            phone = meta.get("phone")
            if provider:
                provider.set_status(id_num, "bad")
                logger.info(f"[VAK-SMS] 已为号码 {phone} (ID: {id_num}) 发起退款释放 (status=bad)")
        except Exception:
            pass
    _ACTIVE_ALLOCATIONS.clear()
