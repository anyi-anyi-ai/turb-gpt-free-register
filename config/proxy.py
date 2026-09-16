# -*- coding: utf-8 -*-
"""
代理池配置

每次注册随机抽取一个代理，保证不同 sid 之间彼此独立，避免风控关联。

协议说明：
    - http:// / https://   HTTP(S) 代理
    - socks5://            SOCKS5（DNS 本地解析，可能泄漏）
    - socks5h://           SOCKS5（DNS 在代理端解析，推荐，避免 DNS-IP 错配）
"""
from config.env_loader import apply_env_overrides
import random


# 本地代理入口；实际出口地区以代理/分流规则为准。
# 推荐使用 socks5h://（DNS 在代理端解析），避免本地 DNS 与出口 IP 地区错配。
PROXY_POOL = [
    "socks5://127.0.0.1:7897",
]

# 按国家划分的高质量/专属代理池（ISO 3166-1 alpha-2 两位大写代码）。
# 默认配置本地 Docker Resin 代理池生成的对应国家 Platform 入口：
PROXY_COUNTRY_POOLS: dict[str, list[str]] = {
    "US": ["http://US:token@127.0.0.1:2260"],
    "DE": ["http://DE:token@127.0.0.1:2260"],
    "JP": ["http://JP:token@127.0.0.1:2260"],
    "GB": ["http://GB:token@127.0.0.1:2260"],
    "SG": ["http://SG:token@127.0.0.1:2260"],
}

# 注册默认首选国家代码。留空时随机从 PROXY_COUNTRY_POOLS 抽取。
DEFAULT_REGISTER_COUNTRY = "JP"

# 严格同国策略：对于已注册账号，如果原 IP 不可用，严格在同国节点池内重选；若对应国家无节点，禁止乱跳其他国家。
STRICT_SAME_COUNTRY_POLICY = True

# 单 IP 注册最大账号数。单个出口 IP / 会话成功注册达到该数量后，自动轮换至下一 IP。
MAX_ACCOUNTS_PER_IP = 2

# 套餐/Plus 试用资格查询与 Codex Agent Token 生成共用这组独立网络策略，
# 避免批量请求被注册代理池中的临时本地代理拖垮，也避免无条件直连造成出口策略失控。
#   auto   = 优先使用 PLAN_CHECK_PROXY 或代理池；本地代理端口未监听时回退直连
#   proxy  = 强制使用 PLAN_CHECK_PROXY 或代理池，失败直接报错
#   direct = 始终直连
PLAN_CHECK_PROXY_MODE = "auto"

# 套餐查询 / Codex Agent Token 生成专用代理。留空时 auto/proxy 模式从 PROXY_POOL 选择。
# 代理可能包含账号密码，因此 WebUI 会把它保存到 .env。
PLAN_CHECK_PROXY = ""

# 查套餐 / 生成 Codex Agent Token 使用独立的短超时和有限重试，避免后台任务长时间卡住。
PLAN_CHECK_TIMEOUT = 15.0
PLAN_CHECK_MAX_ATTEMPTS = 3
PLAN_CHECK_RETRY_DELAY = 2.0

# 新注册账号的权益可能存在短暂同步延迟。首次查询失败，或返回 free 且暂未发现
# Plus 试用资格时，等待该秒数后再复查一次；设为 0 可关闭复查。
PLAN_CHECK_REGISTRATION_RECHECK_DELAY = 2.0

# 自动、手动和批量套餐查询共用同一个后台队列；Codex Agent Token 使用独立队列，
# 但复用这里的网络模式、请求启动间隔与随机抖动，避免批量后台请求过于集中。
PLAN_CHECK_WORKERS = 3
PLAN_CHECK_QUEUE_LIMIT = 500
PLAN_CHECK_MIN_INTERVAL = 1.0
PLAN_CHECK_JITTER = 0.8


def get_available_countries() -> list[str]:
    """获取当前已配置有效代理的国家代码列表（两位大写）。"""
    if not isinstance(PROXY_COUNTRY_POOLS, dict):
        return []
    return sorted([
        str(c).strip().upper()
        for c, proxies in PROXY_COUNTRY_POOLS.items()
        if proxies and isinstance(proxies, (list, tuple))
    ])


def pick_proxy(country: str | None = None, for_registration: bool = False) -> str:
    """从代理池中抽取一个代理 URL。
    
    1. 若 for_registration=True：接入 IPQuotaManager，确保单 IP 注册账号不超过 MAX_ACCOUNTS_PER_IP；
    2. 若传入特定 country，从对应国家池中抽取；
    3. 若未指定 country：
       - 若设置了 DEFAULT_REGISTER_COUNTRY 且池中有配置，使用该国家；
       - 若 PROXY_COUNTRY_POOLS 中有配置，随机选择一个国家；
       - 若国家池为空，回退到通用 PROXY_POOL。
    池为空时返回空串（即不使用代理）。
    """
    if for_registration:
        try:
            from core.ip_quota_manager import ip_quota_manager
            return ip_quota_manager.get_registration_proxy(country=country)
        except Exception:
            pass

    target = str(country or "").strip().upper()
    if target and isinstance(PROXY_COUNTRY_POOLS, dict) and target in PROXY_COUNTRY_POOLS:
        country_pool = PROXY_COUNTRY_POOLS[target]
        if country_pool:
            return random.choice(country_pool)

    if not target:
        try:
            from core.proxy_stats import pick_weighted_country
            weighted_c = pick_weighted_country()
            if weighted_c in PROXY_COUNTRY_POOLS and PROXY_COUNTRY_POOLS[weighted_c]:
                return random.choice(PROXY_COUNTRY_POOLS[weighted_c])
        except Exception:
            pass

    if not target and DEFAULT_REGISTER_COUNTRY and isinstance(PROXY_COUNTRY_POOLS, dict):
        def_c = DEFAULT_REGISTER_COUNTRY.strip().upper()
        if def_c in PROXY_COUNTRY_POOLS and PROXY_COUNTRY_POOLS[def_c]:
            return random.choice(PROXY_COUNTRY_POOLS[def_c])

    countries = get_available_countries()
    if countries:
        c = random.choice(countries)
        return random.choice(PROXY_COUNTRY_POOLS[c])

    return random.choice(PROXY_POOL) if PROXY_POOL else ""


def pick_proxy_for_account(account: dict, check_alive: bool = False) -> str:
    """为已有账号调度代理（严格遵守：同 IP 优先 > 同国轮换 > 严禁跨国跳跃）。
    
    Args:
        account: 账号字典（包含 proxy_used, country, extra_json 等）
        check_alive: 是否强制更换 IP（例如原 IP 已失效）
    """
    proxy_used = str(account.get("proxy_used") or "").strip()
    if proxy_used and not check_alive:
        return proxy_used

    # 获取账号绑定的国家
    country = str(account.get("country") or account.get("geo_country") or "").strip().upper()
    if not country:
        extra_json = account.get("extra_json")
        if isinstance(extra_json, str) and extra_json:
            try:
                import json
                extra = json.loads(extra_json)
                country = str(extra.get("geo_country") or extra.get("country") or "").strip().upper()
                if not country:
                    bp = extra.get("browser_profile") or {}
                    country = str(bp.get("geo_country") or "").strip().upper()
            except Exception:
                pass
        elif isinstance(account.get("extra"), dict):
            extra = account["extra"]
            country = str(extra.get("geo_country") or extra.get("country") or "").strip().upper()
            if not country:
                bp = extra.get("browser_profile") or {}
                country = str(bp.get("geo_country") or "").strip().upper()

    if country:
        if isinstance(PROXY_COUNTRY_POOLS, dict) and country in PROXY_COUNTRY_POOLS and PROXY_COUNTRY_POOLS[country]:
            # 如果是 Resin 代理池，生成/轮换该国的一个 Session 节点
            first_p = str(PROXY_COUNTRY_POOLS[country][0]).strip()
            try:
                from core.ip_quota_manager import ip_quota_manager
                if ip_quota_manager.is_resin_proxy(first_p):
                    import time
                    sess_id = f"rotate_{int(time.time()) % 100000}"
                    return ip_quota_manager._build_resin_session_url(first_p, country, sess_id)
            except Exception:
                pass
            return random.choice(PROXY_COUNTRY_POOLS[country])
        if STRICT_SAME_COUNTRY_POLICY:
            raise RuntimeError(
                f"账号 {account.get('email')} 绑定国家为 {country}，但当前国家代理池为空；"
                f"严格同国策略已阻止跨国请求，避免风控封号"
            )

    # 兜底
    if proxy_used:
        return proxy_used
    return pick_proxy()


# 兼容入口：默认每次进程启动随机选一个，作为本次注册全程的固定代理
PROXY = pick_proxy()

# ---- .env overrides for WebUI editable fields ----
apply_env_overrides(globals(), {
    'PROXY_POOL': 'list_str_multiline',
    'PROXY_COUNTRY_POOLS': 'dict_json',
    'DEFAULT_REGISTER_COUNTRY': 'str',
    'STRICT_SAME_COUNTRY_POLICY': 'bool',
    'MAX_ACCOUNTS_PER_IP': 'int',
    'PLAN_CHECK_PROXY_MODE': 'str',
    'PLAN_CHECK_PROXY': 'str',
    'PLAN_CHECK_TIMEOUT': 'float',
    'PLAN_CHECK_MAX_ATTEMPTS': 'int',
    'PLAN_CHECK_RETRY_DELAY': 'float',
    'PLAN_CHECK_REGISTRATION_RECHECK_DELAY': 'float',
    'PLAN_CHECK_WORKERS': 'int',
    'PLAN_CHECK_QUEUE_LIMIT': 'int',
    'PLAN_CHECK_MIN_INTERVAL': 'float',
    'PLAN_CHECK_JITTER': 'float',
})
PROXY = pick_proxy()

