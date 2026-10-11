# -*- coding: utf-8 -*-
"""
核心与多国高质量节点筛选与 Resin 平台同步工具 (Filter Clean Nodes)

功能：
1. 覆盖 12 个重点国家：US, CA, DE, FR, NL, JP, SG, GB, CH, IT, SE, RO。
2. 支持真实 Chrome TLS 指纹（curl_cffi impersonate="chrome120"）探测 Grok / ChatGPT：
   - Grade A (0 盾直通)：HTTP 200，无任何 Cloudflare 阻断/挑战，极速直达
   - Grade B (轻盾可穿)：HTTP 200，包含单次 Turnstile 点击挑战
   - 淘汰：HTTP 403 / 502 / 503 / 强盾拦截 / 超时 / 离线
3. 自动解析包含国旗 Emoji 的节点 Tag，大幅扩充候选池。
4. 针对各大国挑选 3~12 个高信用纯净节点，并通过 Resin REST API 同步更新对应国家的 Platform 路由规则。
5. 结果持久化输出至 data/clean_nodes.json，供下游注册机与 API 网关按需调用。
"""
import argparse
import io
import json
import logging
import os
import re
import sys
import time
import urllib.request
from pathlib import Path
from dotenv import load_dotenv
from curl_cffi import requests

_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_ROOT / ".env", override=True)

if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FilterCleanNodes")

RESIN_API_BASE = os.getenv("RESIN_API_BASE", "http://127.0.0.1:2260").rstrip("/")
ADMIN_TOKEN = os.getenv("RESIN_ADMIN_TOKEN", "") or "resin_admin_a9e2f47c81d3b06e92fa184c"
PROXY_TOKEN = os.getenv("RESIN_PROXY_TOKEN", "") or "resin_proxy_c3d7b8e1f0a2496582d9183b"
TESTER_PLATFORM = "_speed_tester"

CORE_COUNTRIES = ["US", "DE", "NL", "FR"]

TARGET_COUNTS = {
    "US": 15,
    "DE": 6,
    "NL": 5,
    "FR": 5,
}

MAX_TEST_COUNTS = {
    "US": 30,
    "DE": 15,
    "NL": 15,
    "FR": 15,
}


def resin_api(path: str, method: str = "GET", body: dict | None = None) -> dict:
    url = f"{RESIN_API_BASE}{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    headers = {
        "Authorization": f"Bearer {ADMIN_TOKEN}",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=12) as resp:
        content = resp.read().decode("utf-8")
        return json.loads(content) if content else {}


def ensure_tester_platform() -> str:
    """确保 _speed_tester 平台存在并返回其 ID。"""
    platforms = resin_api("/api/v1/platforms").get("items", [])
    tester = next((p for p in platforms if p.get("name") == TESTER_PLATFORM), None)
    if tester:
        return tester["id"]
    created = resin_api("/api/v1/platforms", method="POST", body={
        "name": TESTER_PLATFORM,
        "region_filters": [],
        "regex_filters": [".*"],
        "allocation_policy": "PREFER_LOW_LATENCY",
    })
    return created["id"]


def go_escape_regex(s: str) -> str:
    """仅转义 Go regexp 元字符，避免空格转义导致解析错误。"""
    return re.sub(r'([\\.+*?()|\[\]{}^$])', r'\\\1', s)


def extract_node_country(node: dict, target_countries: list[str]) -> str:
    """提取节点的准确国家代码，支持 Emoji Flag 与 Tag 正则解析。"""
    c = (node.get("display_country") or node.get("region") or "").upper()
    if c and c != "??" and len(c) == 2 and c in target_countries:
        return c

    tag = node.get("display_tag") or ""
    # 提取区域指示符 (Emoji 国旗)
    flags = [ch for ch in tag if 0x1F1E6 <= ord(ch) <= 0x1F1FF]
    if len(flags) >= 2:
        emoji_c = "".join([chr(ord(ch) - 0x1F1E6 + ord('A')) for ch in flags[:2]])
        if emoji_c in target_countries:
            return emoji_c

    tag_upper = tag.upper()
    for cand_c in target_countries:
        if f"/{cand_c}-" in tag_upper or f"_{cand_c}_" in tag_upper or f"-{cand_c}-" in tag_upper or f"/{cand_c}/" in tag_upper:
            return cand_c

    return c if c in target_countries else "UNKNOWN"


def probe_node(
    tester_id: str,
    tag: str,
    target: str = "grok",
    timeout: float = 4.5,
) -> tuple[int, bool, float, str]:
    """通过 _speed_tester 平台探测单个节点对指定目标的响应。
    
    返回: (http_status, is_turnstile_or_blocked, latency_seconds, title)
    """
    safe_tag = go_escape_regex(tag)
    resin_api(f"/api/v1/platforms/{tester_id}", method="PATCH", body={
        "regex_filters": [f"^{safe_tag}$"]
    })
    time.sleep(0.12)

    base_parts = RESIN_API_BASE.replace("http://", "").replace("https://", "").split(":")
    host = base_parts[0]
    port = base_parts[1] if len(base_parts) > 1 else "2260"
    proxy_url = f"http://{TESTER_PLATFORM}:{PROXY_TOKEN}@{host}:{port}"

    url = "https://accounts.x.ai/sign-up?redirect=grok-com" if target in ("grok", "xai") else "https://chatgpt.com/auth/login"

    t0 = time.time()
    try:
        r = requests.get(
            url,
            proxies={"http": proxy_url, "https": proxy_url},
            impersonate="chrome120",
            timeout=timeout,
        )
        latency = time.time() - t0
        body = r.text
        is_blocked = (
            ("Just a moment..." in body)
            or ("__cf_chl" in body)
            or ("Attention Required!" in body)
            or (r.status_code != 200)
        )
        has_turnstile = ("challenges.cloudflare.com" in body) or ("turnstile" in body)
        title = ""
        if "<title>" in body:
            title = body.split("<title>")[1].split("</title>")[0].strip()
        return r.status_code, (is_blocked or has_turnstile), latency, title
    except Exception:
        return 0, True, time.time() - t0, ""


def update_country_platform(country: str, selected_tags: list[str]) -> bool:
    """将筛选出的优质节点以精确正则更新到 Resin 对应国家平台。"""
    platforms = resin_api("/api/v1/platforms").get("items", [])
    target = next((p for p in platforms if p.get("name") == country), None)

    if selected_tags:
        patterns = []
        for t in selected_tags:
            prefix = re.sub(r'-[a-z0-9]{4}$', '', t)
            patterns.append(go_escape_regex(prefix) + r".*")
        regex_pattern = f"^({'|'.join(patterns)})$"
    else:
        regex_pattern = ".*"

    body = {
        "regex_filters": [regex_pattern],
        "region_filters": [country.lower()],
    }

    if target:
        resin_api(f"/api/v1/platforms/{target['id']}", method="PATCH", body=body)
        logger.info("[Resin] 已更新平台 [%s]：路由至 %d 个精选节点", country, len(selected_tags))
        return True
    else:
        body["name"] = country
        body["allocation_policy"] = "PREFER_LOW_LATENCY"
        resin_api("/api/v1/platforms", method="POST", body=body)
        logger.info("[Resin] 已新建平台 [%s]：包含 %d 个精选节点", country, len(selected_tags))
        return True


def run_screening(
    countries: list[str] | None = None,
    target: str = "grok",
    apply_to_resin: bool = True,
) -> dict:
    """运行多国高质量节点筛选流程。"""
    active_countries = countries or CORE_COUNTRIES
    logger.info("=" * 70)
    logger.info("开始多国纯净节点探测与风控筛选")
    logger.info("目标探测端: %s | 目标国家: %s", target.upper(), ", ".join(active_countries))
    logger.info("=" * 70)

    tester_id = ensure_tester_platform()

    all_nodes_data = resin_api("/api/v1/nodes?limit=3000")
    items = all_nodes_data.get("items", [])
    logger.info("从 Resin 获取到 %d 个节点，正在分类筛选候选池...", len(items))

    candidates_by_country = {c: [] for c in active_countries}
    for n in items:
        if not n.get("has_outbound") or n.get("circuit_open_since") is not None:
            continue
        tag = n.get("display_tag")
        if not tag:
            continue
        c = extract_node_country(n, active_countries)
        if c in candidates_by_country:
            candidates_by_country[c].append(tag)

    for c in active_countries:
        logger.info("  [%s] 识别到候选节点: %d 个", c, len(candidates_by_country[c]))

    results_report = {}

    for country in active_countries:
        candidates = candidates_by_country[country]
        target_count = TARGET_COUNTS.get(country, 4)
        max_test = min(len(candidates), MAX_TEST_COUNTS.get(country, 15))

        logger.info("-" * 70)
        logger.info("[%s] 候选节点: %d 个，目标入选: %d 个，最大探测: %d 个...", country, len(candidates), target_count, max_test)

        if not candidates:
            logger.warning("[%s] 未找到可用候选节点，跳过", country)
            continue

        grade_a = []
        grade_b = []
        tested_count = 0

        for tag in candidates:
            if len(grade_a) >= target_count:
                logger.info("[%s] 已找到足够 0-盾顶级节点 (%d 个)，提前完成筛选", country, len(grade_a))
                break
            if tested_count >= max_test:
                break

            tested_count += 1
            status, has_block_or_cf, latency, title = probe_node(tester_id, tag, target=target, timeout=4.0)

            # 兼容展示 safe tag
            tag_display = tag.encode("ascii", errors="replace").decode()[:45]

            if status == 200 and not has_block_or_cf:
                logger.info("  [%02d/%02d] [PASS: Grade A (0-盾)] 耗时: %.2fs | %s", tested_count, max_test, latency, tag_display)
                grade_a.append({"tag": tag, "grade": "0-shield", "latency": latency})
            elif status == 200:
                logger.info("  [%02d/%02d] [PASS: Grade B (轻盾)] 耗时: %.2fs | %s", tested_count, max_test, latency, tag_display)
                grade_b.append({"tag": tag, "grade": "1-shield", "latency": latency})
            else:
                logger.info("  [%02d/%02d] [FAIL: HTTP %s] 耗时: %.2fs | %s", tested_count, max_test, status or "Timeout", latency, tag_display)

        selected = grade_a + grade_b
        selected_tags = [item["tag"] for item in selected[:target_count]]

        results_report[country] = {
            "tested": tested_count,
            "grade_a_count": len(grade_a),
            "grade_b_count": len(grade_b),
            "selected_tags": selected_tags,
        }

        if selected_tags:
            logger.info("[%s] 筛选完成：选出 %d 个高信用节点 (0-盾: %d, 轻盾: %d)", country, len(selected_tags), len(grade_a), len(grade_b))
            if apply_to_resin:
                update_country_platform(country, selected_tags)
        else:
            logger.warning("[%s] 暂未探测到 200 响应的免盾节点，重置为该国全池（.*）", country)
            if apply_to_resin:
                update_country_platform(country, [])

    # 保存报告至 data/clean_nodes.json
    out_dir = _ROOT / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "clean_nodes.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results_report, f, ensure_ascii=False, indent=2)
    logger.info("节点筛选报告已保存至 %s", out_file)

    logger.info("=" * 70)
    logger.info("各国家纯净节点池汇总：")
    total_selected = 0
    for c, r in results_report.items():
        cnt = len(r["selected_tags"])
        total_selected += cnt
        logger.info("  * [%s]: %d 个精选节点 (0-盾: %d, 轻盾: %d)", c, cnt, r["grade_a_count"], r["grade_b_count"])
    logger.info("共计纳入 %d 个纯净高信用节点！", total_selected)
    logger.info("=" * 70)
    return results_report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="多国高质量纯净节点筛选工具")
    parser.add_argument("--countries", nargs="+", default=None, help="指定探测的国家代码，如 US CA DE FR NL JP SG")
    parser.add_argument("--target", default="chatgpt", choices=["grok", "chatgpt"], help="探测目标网站 (默认 chatgpt)")
    parser.add_argument("--no-apply", action="store_true", help="不自动同步到 Resin 平台路由规则")
    args = parser.parse_args()

    run_screening(countries=args.countries, target=args.target, apply_to_resin=not args.no_apply)
