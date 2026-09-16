# -*- coding: utf-8 -*-
"""
单 IP 注册配额与会话轮换管理器 (IP Quota Manager)

核心机制：
1. 单个出口 IP / 会话（Session）在成功注册达到指定数量（默认 1~2 个账号，由 MAX_ACCOUNTS_PER_IP 控制）后，
   自动切换到下一个全新 IP 会话，避免同一 IP 频繁注册触发风控。
2. 支持本地 Docker Resin 聚合网关的动态 Session 轮换（格式：http://<Country>.<Session>:<Token>@127.0.0.1:2260），
   不同 Session 自动分配同国不同出口 IP，且各自具备 7 天粘性。
3. 支持常规静态多节点列表：自动跟踪每个节点已注册账号数，未用满优先，用满自动向后轮换。
4. 状态持久化到 SQLite 数据库 storage_meta 表，保证服务重启后已注册次数不丢失。
"""
import json
import logging
import re
import threading
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger(__name__)

_DEFAULT_MAX_ACCOUNTS_PER_IP = 2
_STORAGE_META_KEY = "ip_quota_manager_state"


def _normalize_proxy_url(proxy_url: str | None) -> str:
    if not proxy_url:
        return ""
    p = str(proxy_url).strip()
    return p


class IPQuotaManager:
    """单 IP 注册配额与轮换管理器。"""

    def __init__(self):
        self._lock = threading.RLock()
        self._state = {
            "proxy_counts": {},       # {normalized_proxy_url: {"count": int, "country": str, "last_used": str, "last_email": str}}
            "country_sessions": {},   # {country_code: int} 当前活跃的会话序号，从 1 开始
        }
        self._in_flight: dict[str, int] = {}
        self._country_rr_idx: int = 0
        self._initialized = False

    def _ensure_loaded(self) -> None:
        if self._initialized:
            return
        with self._lock:
            if self._initialized:
                return
            self._load_from_storage()
            self._initialized = True

    def _load_from_storage(self) -> None:
        try:
            from core.db import _sqlite_conn
            with _sqlite_conn() as conn:
                row = conn.execute(
                    "SELECT value FROM storage_meta WHERE key = ? LIMIT 1",
                    (_STORAGE_META_KEY,),
                ).fetchone()
                if row and row[0]:
                    loaded = json.loads(row[0])
                    if isinstance(loaded, dict):
                        self._state["proxy_counts"] = loaded.get("proxy_counts") or {}
                        self._state["country_sessions"] = loaded.get("country_sessions") or {}
                        logger.info(
                            "[IP配额] 成功加载历史配额状态：已跟踪 %d 个代理/会话，%d 个国家会话游标",
                            len(self._state["proxy_counts"]),
                            len(self._state["country_sessions"]),
                        )
                        return
        except Exception as exc:
            logger.warning("[IP配额] 从存储加载配额状态失败，将初始化为空状态：%s: %s", type(exc).__name__, exc)

    def _save_to_storage(self) -> None:
        try:
            from core.db import _sqlite_conn
            payload = json.dumps(self._state, ensure_ascii=False)
            with _sqlite_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO storage_meta (key, value) VALUES (?, ?)",
                    (_STORAGE_META_KEY, payload),
                )
        except Exception as exc:
            logger.warning("[IP配额] 保存配额状态到存储失败：%s: %s", type(exc).__name__, exc)

    @property
    def max_accounts_per_ip(self) -> int:
        try:
            import config.proxy as proxy_cfg
            val = getattr(proxy_cfg, "MAX_ACCOUNTS_PER_IP", _DEFAULT_MAX_ACCOUNTS_PER_IP)
            return max(1, int(val))
        except Exception:
            return _DEFAULT_MAX_ACCOUNTS_PER_IP

    def is_resin_proxy(self, proxy_url: str) -> bool:
        """判断一个代理 URL 是否为 Resin 聚合网关入口。"""
        if not proxy_url:
            return False
        p = urlsplit(proxy_url)
        # 常见 Resin 标记：端口为 2260，或密码含 resin_proxy
        is_port = (p.port == 2260)
        is_token = bool(p.password and "resin_proxy" in p.password)
        return is_port or is_token

    def _build_resin_session_url(self, base_proxy_url: str, country: str, session_idx: int) -> str:
        """构建 Resin 带 Session 的代理入口 URL。"""
        p = urlsplit(base_proxy_url)
        c_code = country.strip().upper()
        session_user = f"{c_code}.sess_{session_idx}"
        pwd = p.password or ""
        host = p.hostname or "127.0.0.1"
        port = p.port or 2260
        netloc = f"{session_user}:{pwd}@{host}:{port}" if pwd else f"{session_user}@{host}:{port}"
        return urlunsplit((p.scheme or "http", netloc, p.path, p.query, p.fragment))

    def get_registration_proxy(self, country: str | None = None) -> str:
        """为新的注册流程挑选一个尚未达到配额上限的代理/会话。
        
        若当前出口 IP 已成功注册达到 MAX_ACCOUNTS_PER_IP，自动轮换到下一个 IP/会话。
        """
        self._ensure_loaded()
        with self._lock:
            import config.proxy as proxy_cfg

            # 1. 确定国家：优先遵循指定 country；未指定时遵循配置的国家百分比权重智能分流
            target_country = str(country or "").strip().upper()
            if not target_country:
                try:
                    from core.proxy_stats import pick_weighted_country
                    target_country = pick_weighted_country()
                except Exception:
                    target_country = ""

            if not target_country:
                def_country = str(getattr(proxy_cfg, "DEFAULT_REGISTER_COUNTRY", "") or "").strip().upper()
                if def_country and def_country in proxy_cfg.get_available_countries():
                    target_country = def_country
                else:
                    avail = proxy_cfg.get_available_countries()
                    if avail:
                        target_country = avail[self._country_rr_idx % len(avail)]
                        self._country_rr_idx += 1

            country_pools = getattr(proxy_cfg, "PROXY_COUNTRY_POOLS", {}) or {}
            proxies_in_country = country_pools.get(target_country) if isinstance(country_pools, dict) else None

            limit = self.max_accounts_per_ip

            # 2. 如果对应国家配置了代理池
            if target_country and proxies_in_country and isinstance(proxies_in_country, (list, tuple)):
                first_proxy = str(proxies_in_country[0]).strip()

                # 分支 A：Resin 聚合网关代理（支持动态 Country.Session 切换全新独立出口 IP）
                if self.is_resin_proxy(first_proxy):
                    current_idx = int(self._state["country_sessions"].get(target_country, 1))
                    while True:
                        candidate_url = self._build_resin_session_url(first_proxy, target_country, current_idx)
                        norm = _normalize_proxy_url(candidate_url)
                        used_count = self._state["proxy_counts"].get(norm, {}).get("count", 0)
                        in_flight = self._in_flight.get(norm, 0)
                        active_count = used_count + in_flight
                        if active_count < limit:
                            self._state["country_sessions"][target_country] = current_idx
                            self._in_flight[norm] = in_flight + 1
                            logger.info(
                                "[IP配额] 锁定国家 %s 会话 sess_%d 入口 [%s] (已完成 %d, 在途 %d, 配额 %d)",
                                target_country, current_idx, norm.split("@")[-1] if "@" in norm else norm,
                                used_count, in_flight + 1, limit,
                            )
                            return candidate_url
                        # 已达上限或正在并发使用中，步进到下一个 Session
                        logger.info(
                            "[IP配额] 国家 %s 的 Resin 会话 sess_%d 占用中 (已完成 %d, 在途 %d, 上限 %d)，自动轮换至 sess_%d",
                            target_country, current_idx, used_count, in_flight, limit, current_idx + 1,
                        )
                        current_idx += 1
                        self._state["country_sessions"][target_country] = current_idx
                        self._save_to_storage()

                # 分支 B：常规静态多节点代理列表
                else:
                    candidates = []
                    for prx in proxies_in_country:
                        norm = _normalize_proxy_url(prx)
                        used_count = self._state["proxy_counts"].get(norm, {}).get("count", 0)
                        in_flight = self._in_flight.get(norm, 0)
                        candidates.append((used_count + in_flight, used_count, in_flight, norm, prx))

                    # 优先挑选 (已用 + 在途) < limit 的第一个节点
                    for active_count, used_count, in_flight, norm, raw_prx in candidates:
                        if active_count < limit:
                            self._in_flight[norm] = in_flight + 1
                            return raw_prx

                    # 若全部节点都已用满，记录警告并选择已用次数最少的节点兜底
                    candidates.sort(key=lambda x: x[0])
                    least_used = candidates[0][4]
                    logger.warning(
                        "[IP配额] 国家 %s 代理池中所有 %d 个节点均已达到单 IP 最大注册次数 (%d 次)！兜底复用节点：%s",
                        target_country, len(proxies_in_country), limit, least_used,
                    )
                    return least_used

            # 3. 兜底逻辑：常规 pick_proxy
            return proxy_cfg.pick_proxy(country=target_country)

    def release_in_flight(self, proxy_url: str | None) -> None:
        """当注册失败或中止时，安全释放在途预占槽位。"""
        if not proxy_url:
            return
        norm = _normalize_proxy_url(proxy_url)
        with self._lock:
            if norm in self._in_flight:
                self._in_flight[norm] = max(0, self._in_flight[norm] - 1)
                logger.debug("[IP配额] 释放在途预占: %s (剩余在途: %d)", norm.split("@")[-1] if "@" in norm else norm, self._in_flight[norm])

    def record_proxy_failure(
        self,
        proxy_url: str | None,
        reason: str | None = None,
        country: str | None = None,
    ) -> None:
        """当代理遇到网络隧道断开、超时或死节点时调用，立即轮换坏会话并释放在途槽位。"""
        if not proxy_url:
            return
        self._ensure_loaded()
        norm = _normalize_proxy_url(proxy_url)
        with self._lock:
            # 1. 释放对应的在途预占
            if norm in self._in_flight:
                self._in_flight[norm] = max(0, self._in_flight[norm] - 1)
                logger.debug("[IP配额] 释放在途预占: %s (剩余在途: %d)", norm.split("@")[-1] if "@" in norm else norm, self._in_flight[norm])

            # 2. 如果是 Resin 聚合网关，提取出国家与 session 序号，并立即步进国家会话游标
            if self.is_resin_proxy(proxy_url):
                p = urlsplit(proxy_url)
                user = (p.username or "").strip()
                inferred_country = country
                current_sess_idx = None
                if "." in user:
                    c_part, s_part = user.split(".", 1)
                    if not inferred_country:
                        inferred_country = c_part.upper()
                    if s_part.startswith("sess_"):
                        try:
                            current_sess_idx = int(s_part[5:])
                        except Exception:
                            pass

                if inferred_country:
                    c_code = inferred_country.strip().upper()
                    prev_idx = int(self._state["country_sessions"].get(c_code, 1))
                    if current_sess_idx is not None and current_sess_idx >= prev_idx:
                        new_idx = current_sess_idx + 1
                    else:
                        new_idx = prev_idx + 1
                    self._state["country_sessions"][c_code] = new_idx
                    self._save_to_storage()
                    logger.warning(
                        "[IP配额] 检测到代理会话故障 (%s)，国家 %s 会话已从 sess_%s 自动步进至 sess_%d，避免后续任务重复踩坑！",
                        reason or "网络异常", c_code, current_sess_idx if current_sess_idx is not None else prev_idx, new_idx,
                    )
            else:
                logger.warning(
                    "[IP配额] 代理节点故障 (%s): %s",
                    reason or "网络异常", norm.split("@")[-1] if "@" in norm else norm,
                )

    def record_account_success(
        self,
        proxy_url: str | None,
        country: str | None = None,
        email: str | None = None,
    ) -> int:
        """当一个账号通过该代理成功注册并保存时调用此函数，累加配额计数并释放在途槽位。"""
        if not proxy_url:
            return 0
        self._ensure_loaded()
        norm = _normalize_proxy_url(proxy_url)
        with self._lock:
            # 释放对应的在途预占
            if norm in self._in_flight:
                self._in_flight[norm] = max(0, self._in_flight[norm] - 1)

            entry = self._state["proxy_counts"].get(norm) or {"count": 0}
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["country"] = str(country or entry.get("country") or "").upper()
            entry["last_used"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            if email:
                entry["last_email"] = str(email)
            self._state["proxy_counts"][norm] = entry

            limit = self.max_accounts_per_ip
            current_count = entry["count"]
            logger.info(
                "[IP配额] 代理/会话 [%s] 成功注册数 +1 (当前: %d/%d, 账号: %s)",
                norm.split("@")[-1] if "@" in norm else norm,
                current_count,
                limit,
                email or "未知",
            )

            if current_count >= limit:
                logger.info(
                    "[IP配额] 代理/会话 [%s] 已达到单 IP 注册上限 (%d/%d)，下次同国注册将自动切换至新 IP！",
                    norm.split("@")[-1] if "@" in norm else norm,
                    current_count,
                    limit,
                )

            self._save_to_storage()
            return current_count

    def get_proxy_count(self, proxy_url: str) -> int:
        """查询指定代理当前的已注册账号数。"""
        self._ensure_loaded()
        norm = _normalize_proxy_url(proxy_url)
        with self._lock:
            return int(self._state["proxy_counts"].get(norm, {}).get("count", 0))

    def get_summary(self) -> dict:
        """获取当前配额管理器的状态摘要（供 WebUI 或日志展示）。"""
        self._ensure_loaded()
        with self._lock:
            return {
                "max_accounts_per_ip": self.max_accounts_per_ip,
                "tracked_proxies_count": len(self._state["proxy_counts"]),
                "country_sessions": dict(self._state["country_sessions"]),
                "proxy_counts": {
                    k: dict(v) for k, v in self._state["proxy_counts"].items()
                },
            }

    def reset_quota(self, country: str | None = None) -> None:
        """重置指定国家或所有国家的会话游标与计数。"""
        self._ensure_loaded()
        with self._lock:
            c = str(country or "").strip().upper()
            if c:
                self._state["country_sessions"][c] = 1
                to_del = [k for k, v in self._state["proxy_counts"].items() if v.get("country") == c]
                for k in to_del:
                    del self._state["proxy_counts"][k]
            else:
                self._state["country_sessions"].clear()
                self._state["proxy_counts"].clear()
            self._save_to_storage()
            logger.info("[IP配额] 已重置配额状态 (country=%s)", country or "全部")


# 单例实例
ip_quota_manager = IPQuotaManager()
