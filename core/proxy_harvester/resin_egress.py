# -*- coding: utf-8 -*-
"""Proxy Harvester: Resin 上游注册出口与独立 Session 粘性调度器。"""
from __future__ import annotations

import logging
import os
import socket
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from config.proxy import PROXY_POOL
from core.continent_proxy_router import check_proxy_tunnel_alive

logger = logging.getLogger("harvester.egress")

DEFAULT_RESIN_HOST = os.getenv("RESIN_HOST", "127.0.0.1")
DEFAULT_RESIN_PORT = int(os.getenv("RESIN_PORT", "2260"))
DEFAULT_RESIN_PROXY_TOKEN = os.getenv("RESIN_PROXY_TOKEN", "resin_proxy_c3d7b8e1f0a2496582d9183b")
DEFAULT_RESIN_PLATFORM = os.getenv("RESIN_DEFAULT_PLATFORM", "JP")


class ResinEgressManager:
    """Resin 出口调度器：实现'以代理养代理'，单账号锁定独立会话，换账号平滑切 IP。"""

    def __init__(
        self,
        platform: str = DEFAULT_RESIN_PLATFORM,
        host: str = DEFAULT_RESIN_HOST,
        port: int = DEFAULT_RESIN_PORT,
        token: str = DEFAULT_RESIN_PROXY_TOKEN,
    ):
        self.platform = platform
        self.host = host
        self.port = port
        self.token = token

    def find_active_platform(self, target_host: str = "www.webshare.io") -> str | None:
        """从 Resin 候选平台中自动识别高信用、活跃可用平台 (优先 Default, SG, DE)。"""
        candidates = []
        if self.platform and self.platform not in ("US", "Dedicated"):
            candidates.append(self.platform)
        for p_name in ("Default", "SG", "DE", "FAST_UPSTREAM"):
            if p_name not in candidates:
                candidates.append(p_name)

        for cand in candidates:
            test_url = f"http://{cand}:{self.token}@{self.host}:{self.port}"
            try:
                if check_proxy_tunnel_alive(test_url, target_host=target_host, timeout=3.0):
                    return cand
            except Exception:
                continue

        for cand in candidates:
            test_url = f"http://{cand}:{self.token}@{self.host}:{self.port}"
            try:
                if check_proxy_tunnel_alive(test_url, target_host="api.ipify.org", timeout=3.0):
                    return cand
            except Exception:
                continue
        return None

    def is_resin_listening(self) -> bool:
        """检测 Resin 端口是否在线且具备存活可用节点。"""
        return self.find_active_platform() is not None

    def get_account_proxy(self, account_index: int, batch_tag: str = "batch", target_url: str | None = None) -> str | None:
        """为特定账号分配带有专属 Session 粘性锁定的纯净代理出口。
        
        执行 pre-flight 门禁与真实站点直连检测，快速返回响应最优出口。
        """
        probe_target = target_url or "https://dashboard.proxyscrape.com/v2/sign-up"
        site_name = "ProxyScrape" if "proxyscrape" in probe_target.lower() else "Webshare"
        import time, uuid
        from core.proxy_quality import check_proxy_health_and_fraud

        candidates = []
        if self.platform and self.platform not in ("US", "Dedicated", "FAST_UPSTREAM"):
            candidates.append(self.platform)
        for p in ("Default", "JP", "NL", "DE"):
            if p not in candidates:
                candidates.append(p)

        for plat in candidates:
            for _attempt in range(2):
                tag = f"sess_{uuid.uuid4().hex[:6]}"
                test_proxy = f"http://{plat}.{tag}:{self.token}@{self.host}:{self.port}"
                try:
                    t0 = time.time()
                    opener = urllib.request.build_opener(urllib.request.ProxyHandler({"http": test_proxy, "https": test_proxy}))
                    req = urllib.request.Request(probe_target, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
                    status_code = None
                    try:
                        resp = opener.open(req, timeout=4.0)
                        status_code = resp.status
                    except urllib.error.HTTPError as he:
                        status_code = he.code
                    except Exception:
                        pass

                    if status_code in (200, 301, 302, 403, 401):
                        latency = round(time.time() - t0, 2)
                        verdict = ""
                        ip = ""
                        isp = ""
                        try:
                            q = check_proxy_health_and_fraud(test_proxy, timeout=6.0)
                            verdict = q.get("verdict", "")
                            ip = q.get("ip", "")
                            isp = q.get("isp", "")
                            logger.info(f"🌐 [上游出口] 命中 Resin 粘性出口 ({plat}.{tag}) [{verdict}] IP={ip} ISP={isp} {site_name}直连 延迟={latency}s")
                        except Exception as ex:
                            logger.warning(f"⚠️ [上游出口] 检测 Resin 出口 ({plat}.{tag}) 探测异常: {ex}")
                            continue

                        if verdict != "CLEAN_RESIDENTIAL":
                            logger.warning(f"⚠️ [出口审计] 非纯净住宅出口 [{verdict}] IP={ip} ISP={isp}，自动轮换纯净住宅节点...")
                            continue

                        return test_proxy
                except Exception:
                    continue

        # 兜底：只要 Resin 本地端口处于监听状态，使用高可用 Default 住宅粘性会话
        try:
            with socket.create_connection((self.host, self.port), timeout=1.0):
                tag = f"sess_{uuid.uuid4().hex[:6]}"
                fallback_resin = f"http://Default.{tag}:{self.token}@{self.host}:{self.port}"
                logger.info(f"🌐 [上游出口] 兜底保底 Resin 住宅粘性会话: {fallback_resin}")
                return fallback_resin
        except Exception:
            pass

        # 2. 检查高质量专属代理池 (如 Azure 独享学生节点)
        try:
            from config.proxy import PROXY_COUNTRY_POOLS
            for country, p_list in PROXY_COUNTRY_POOLS.items():
                for p in p_list:
                    if "7890" not in p and check_proxy_tunnel_alive(p, target_host="api.ipify.org", timeout=3.0):
                        logger.info(f"🌐 [上游出口] 命中专属高信用节点 ({country}): {p}")
                        return p
        except Exception:
            pass

        # 3. 回退至本地活动代理池 (严格排除 7890)
        logger.warning(f"⚠️ [上游出口] Resin ({self.host}:{self.port}) 未连通，检测本地代理池...")
        for p in PROXY_POOL:
            if "7890" not in p and check_proxy_tunnel_alive(p, target_host="api.ipify.org", timeout=2.5):
                logger.info(f"🌐 [上游出口] 成功回退至本地可用代理: {p}")
                return p

        logger.warning("⚠️ [上游出口] 未探测到有效代理，严禁使用 7890，返回 None")
        return None
