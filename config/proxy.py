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


# 本地代理入口；优先走本地链式代理 (127.0.0.1:28888) 或 Resin 粘性网关 (127.0.0.1:2260)。
# 严禁直接裸连非隔离 VPS 出口。
PROXY_POOL = [
    "http://127.0.0.1:28888",
    "http://US:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260",
]

# 按国家划分的高质量/专属代理池（ISO 3166-1 alpha-2 两位大写代码）。
# 严格仅保留实测具备有效 0-盾直通 ChatGPT 节点的国家：
PROXY_COUNTRY_POOLS: dict[str, list[str]] = {
    "SG": [
        "http://azureuser:AzStudent@liT0SdIuUc!@4.193.130.57:10808",
        "http://SG:resin_proxy_c3d7b8e1f0a2496582d9183b@4.193.130.57:2260",
    ],
    "US": ["http://US:resin_proxy_c3d7b8e1f0a2496582d9183b@4.193.130.57:2260"],
    "JP": ["http://JP:resin_proxy_c3d7b8e1f0a2496582d9183b@4.193.130.57:2260"],
    "DE": ["http://DE:resin_proxy_c3d7b8e1f0a2496582d9183b@4.193.130.57:2260"],
    "NL": ["http://NL:resin_proxy_c3d7b8e1f0a2496582d9183b@4.193.130.57:2260"],
    "GB": ["http://GB:resin_proxy_c3d7b8e1f0a2496582d9183b@4.193.130.57:2260"],
}

# 注册默认首选国家代码。留空时随机从 PROXY_COUNTRY_POOLS 抽取。
DEFAULT_REGISTER_COUNTRY = "SG"

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


# ==============================================================================
# 链式代理与动态住宅 IP 出口配置 (88.88 中转 + 7天 Dedicated 专享出口 / udealproxy)
# ==============================================================================
# 【链式代理 / 动态住宅 IP 开关与模式】
#   "dedicated"     = 88.88 中转 ➔ 7天 Dedicated 专享节点出口（纯 Python 双跳，零本地软件依赖，强烈推荐）
#   "api"           = 88.88 中转 ➔ 第三方动态住宅 API 提取出口
#   "auto"  / True  = 自动容灾模式：优先使用 7天 Dedicated 专享出口，若提取失败平滑切回本地 Resin 粘性代理池
#   "forced"        = 强制模式：若链式专享出口提取失败直接报错阻断，严禁降级
#   "false" / False = 彻底关闭链式中转，注册直接走本地 Resin 粘性代理池 (127.0.0.1:2260)
DYNAMIC_RESIDENTIAL_ENABLED: str = "dedicated"

# 动态住宅提取 API 接口地址（如 rapidproxy 接口）
DYNAMIC_RESIDENTIAL_API_URL: str = "https://www.rapidproxy.io/api/proxy/get_proxy_ip?num=1&regions=GLOBAL&protocol=http&return_type=txt&lb=1&sb="

# 动态住宅地区提取权重分配（默认 100% 美国优质住宅出口）
DYNAMIC_RESIDENTIAL_REGIONS: list[str] = ["US:10"]

# 单个动态住宅出口 IP 允许注册的最大账号数（默认 1，节约住宅 IP 并防止同 IP 关联封禁）
DYNAMIC_RESIDENTIAL_MAX_ACCOUNTS_PER_IP: int = 1

# 本地透明链式中转代理监听地址与端口（注册机指纹浏览器统一连到此端口）
DYNAMIC_RESIDENTIAL_LOCAL_HOST: str = "127.0.0.1"
DYNAMIC_RESIDENTIAL_LOCAL_PORT: int = 28888

# 高速上游网关入口（用于加速本地连接动态住宅节点，由 88.88 节点承载）
DYNAMIC_RESIDENTIAL_UPSTREAM_PROXY: str = "http://FAST_UPSTREAM:resin_proxy_c3d7b8e1f0a2496582d9183b@127.0.0.1:2260"

# 单个住宅 IP 最大保持生命周期（秒），超时自动重新提取（默认 900 秒 = 15 分钟）
DYNAMIC_RESIDENTIAL_MAX_LIFETIME_SECONDS: float = 900.0


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
    
    1. 若 for_registration=True：
       - 若启用 DYNAMIC_RESIDENTIAL_ENABLED ("dedicated" / "auto" / "forced" / "api" / "true")：
         优先从链式代理管理器获取本地双跳代理出口 (88.88 中转 -> 7天 Dedicated 专享出口)；
         - "forced" 模式下若提取失败，直接抛错终止；
         - 其他模式下若提取失败，平滑降级到 IPQuotaManager 本地粘性代理池；
       - 若 DYNAMIC_RESIDENTIAL_ENABLED 为 "false"，直接接入 IPQuotaManager，确保单 IP 注册账号不超过 MAX_ACCOUNTS_PER_IP；
    2. 若传入特定 country，从对应国家池中抽取；
    3. 若未指定 country：
       - 若设置了 DEFAULT_REGISTER_COUNTRY 且池中有配置，使用该国家；
       - 若 PROXY_COUNTRY_POOLS 中有配置，按前端配置的权重加权选择国家；
       - 若国家池为空，回退到通用 PROXY_POOL。
    池为空时返回空串（即不使用代理）。
    """
    if for_registration:
        dyn_mode = str(globals().get("DYNAMIC_RESIDENTIAL_ENABLED", "dedicated")).strip().lower()
        if dyn_mode in ("true", "1", "yes", "on", "auto", "forced", "dedicated", "api"):
            try:
                from core.dynamic_residential_proxy import dynamic_residential_manager
                if dynamic_residential_manager.enabled:
                    p = dynamic_residential_manager.get_registration_proxy(country=country)
                    if p:
                        return p
            except Exception as exc:
                if dyn_mode == "forced":
                    raise RuntimeError(f"[链式代理] 强制模式 (forced) 生效中，但提取出口 IP 失败: {exc}")
                # auto/dedicated 模式静默回退到粘性代理池
                pass

        try:
            from core.ip_quota_manager import ip_quota_manager
            return ip_quota_manager.get_registration_proxy(country=country)
        except Exception:
            pass

    target = str(country or "").strip().upper()
    _country_map = {
        "UNITED STATES": "US", "USA": "US", "AMERICA": "US",
        "JAPAN": "JP",
        "GERMANY": "DE",
        "UNITED KINGDOM": "US", "UK": "US", "GB": "US",
        "FI": "US", "FINLAND": "US",
    }
    target = _country_map.get(target, target)
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
    """为已有账号调度代理（严格遵守：Level 1 同 IP 优先 > Level 2 同国轮换 > Level 3 同大洲递归 > 严禁跨大洲跳跃）。
    
    Args:
        account: 账号字典（包含 proxy_used, country, extra_json 等）
        check_alive: 是否强制更换 IP（例如原 IP 已失效）
    """
    country = (account.get("country") or "").strip().upper()
    if STRICT_SAME_COUNTRY_POLICY and country and country not in PROXY_COUNTRY_POOLS:
        raise RuntimeError(f"严格同国策略已阻止跨国请求：未配置国家 [{country}] 的代理池")

    try:
        from core.continent_proxy_router import resolve_account_proxy_3tier
        proxy_url, target_country, tier = resolve_account_proxy_3tier(
            account, check_alive=check_alive, force_rotate=check_alive
        )
        return proxy_url
    except Exception as exc:
        if STRICT_SAME_COUNTRY_POLICY:
            raise
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
    'DYNAMIC_RESIDENTIAL_ENABLED': 'str',
    'DYNAMIC_RESIDENTIAL_API_URL': 'str',
    'DYNAMIC_RESIDENTIAL_REGIONS': 'list_str',
    'DYNAMIC_RESIDENTIAL_MAX_ACCOUNTS_PER_IP': 'int',
    'DYNAMIC_RESIDENTIAL_LOCAL_HOST': 'str',
    'DYNAMIC_RESIDENTIAL_LOCAL_PORT': 'int',
    'DYNAMIC_RESIDENTIAL_UPSTREAM_PROXY': 'str',
    'DYNAMIC_RESIDENTIAL_MAX_LIFETIME_SECONDS': 'float',
})
PROXY = pick_proxy()

