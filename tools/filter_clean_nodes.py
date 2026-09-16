# -*- coding: utf-8 -*-
"""
核心国家高质量节点筛选与 Resin 平台同步工具 (Filter Clean Nodes)

功能：
1. 聚焦 6 个核心国家：US (美国), GB (英国), JP (日本), SG (新加坡), DE (德国), CA (加拿大)。
2. 使用真实的 Chrome TLS 指纹（curl_cffi impersonate="chrome120"）探测 ChatGPT 登录页：
   - Grade A (0 盾直通)：HTTP 200，无任何 Cloudflare 挑战，直达登录框（顶级高信用住宅/未滥用 IP）
   - Grade B (轻盾可穿)：HTTP 200，包含单次 Turnstile 点击挑战（可通过自动穿透模块通过）
   - 淘汰：HTTP 403 / 强盾拦截 / 超时 / 离线
3. 自动为每个国家挑选 2~5 个顶级高信用节点，并通过 Resin REST API 同步更新对应国家的 Platform 路由规则。
"""
import json
import logging
import os
import re
import sys
import time
import urllib.request
from curl_cffi import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("FilterCleanNodes")

RESIN_API_BASE = os.getenv("RESIN_API_BASE", "http://127.0.0.1:2260").rstrip("/")
ADMIN_TOKEN = os.getenv("RESIN_ADMIN_TOKEN", "")
PROXY_TOKEN = os.getenv("RESIN_PROXY_TOKEN", "")
TESTER_PLATFORM = "_speed_tester"

CORE_COUNTRIES = ["GB", "JP", "SG", "US", "DE", "CA"]
TARGET_PER_COUNTRY = 3  # 每个国家筛选的目标优质节点数
MAX_TEST_PER_COUNTRY = 15  # 每个国家最多测试的候选节点数


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


def probe_node_chatgpt(tester_id: str, tag: str, timeout: float = 6.0) -> tuple[int, bool, float, str]:
    """通过 _speed_tester 平台探测单个节点对 ChatGPT 登录页的响应。
    
    返回: (http_status, is_turnstile, latency_seconds, title)
    """
    # 动态把 _speed_tester 的出站锁定为该单个节点
    resin_api(f"/api/v1/platforms/{tester_id}", method="PATCH", body={
        "regex_filters": [f"^{re.escape(tag)}$"]
    })
    time.sleep(0.15)

    base_parts = RESIN_API_BASE.replace("http://", "").replace("https://", "").split(":")
    host = base_parts[0]
    port = base_parts[1] if len(base_parts) > 1 else "2260"
    proxy_url = f"http://{TESTER_PLATFORM}:{PROXY_TOKEN}@{host}:{port}"

    t0 = time.time()
    try:
        r = requests.get(
            "https://chatgpt.com/auth/login",
            proxies={"http": proxy_url, "https": proxy_url},
            impersonate="chrome120",
            timeout=timeout,
        )
        latency = time.time() - t0
        body = r.text
        has_turnstile = (
            ("challenges.cloudflare.com" in body)
            or ("__cf_chl" in body)
            or ("Just a moment..." in body)
        )
        title = ""
        if "<title>" in body:
            title = body.split("<title>")[1].split("</title>")[0].strip()
        return r.status_code, has_turnstile, latency, title
    except Exception:
        return 0, True, time.time() - t0, ""


def update_country_platform(country: str, selected_tags: list[str]) -> bool:
    """将筛选出的优质节点以精确正则更新到 Resin 对应国家平台。"""
    platforms = resin_api("/api/v1/platforms").get("items", [])
    target = next((p for p in platforms if p.get("name") == country), None)
    
    # 构建精准且耐订阅刷新的前缀正则（兼容末尾随机哈希如 -qqxd）
    if selected_tags:
        patterns = []
        for t in selected_tags:
            prefix = re.sub(r'-[a-z0-9]{4}$', '', t)
            patterns.append(re.escape(prefix) + r".*")
        regex_pattern = f"^({'|'.join(patterns)})$"
    else:
        regex_pattern = ".*"

    body = {
        "regex_filters": [regex_pattern],
        "region_filters": [country.lower()],
    }

    if target:
        resin_api(f"/api/v1/platforms/{target['id']}", method="PATCH", body=body)
        logger.info("[Resin] 已更新平台 [%s]：路由至 %d 个精选高信用节点", country, len(selected_tags))
        return True
    else:
        body["name"] = country
        body["allocation_policy"] = "PREFER_LOW_LATENCY"
        resin_api("/api/v1/platforms", method="POST", body=body)
        logger.info("[Resin] 已新建平台 [%s]：包含 %d 个精选高信用节点", country, len(selected_tags))
        return True


def run_screening(apply_to_resin: bool = True) -> dict:
    """运行多国高质量节点筛选流程。"""
    logger.info("=" * 70)
    logger.info("开始 6 国高质量 ChatGPT 节点风控级筛选")
    logger.info("目标国家: %s | 目标节点数: %d/国", ", ".join(CORE_COUNTRIES), TARGET_PER_COUNTRY)
    logger.info("=" * 70)

    tester_id = ensure_tester_platform()

    # 拉取 Resin 所有可用节点
    all_nodes_data = resin_api("/api/v1/nodes?limit=3000")
    items = all_nodes_data.get("items", [])
    logger.info("从 Resin 获取到 %d 个节点，正在分类筛选候选池...", len(items))

    # 按国家分组候选节点
    candidates_by_country = {c: [] for c in CORE_COUNTRIES}
    for n in items:
        if not n.get("has_outbound") or n.get("circuit_open_since") is not None:
            continue
        tag = n.get("display_tag")
        if not tag:
            continue
        # 匹配国家
        c = (n.get("display_country") or n.get("region") or "").upper()
        # 优先以 tag 中的国家代码为准（避免 IP 归属地库误报）
        tag_upper = tag.upper()
        detected_c = None
        for cand_c in CORE_COUNTRIES:
            if f"/{cand_c}-" in tag_upper or f"_{cand_c}_" in tag_upper:
                detected_c = cand_c
                break
        if detected_c:
            c = detected_c
        if c in candidates_by_country:
            candidates_by_country[c].append(tag)

    results_report = {}

    for country in CORE_COUNTRIES:
        candidates = candidates_by_country[country]
        logger.info("-" * 70)
        logger.info("[%s] 候选节点数: %d，开始风控探测 (最多测试 %d 个)...", country, len(candidates), MAX_TEST_PER_COUNTRY)

        if not candidates:
            logger.warning("[%s] 未找到可用候选节点，跳过", country)
            continue

        grade_a = []  # 0 盾
        grade_b = []  # 1 盾

        tested_count = 0
        for tag in candidates:
            if len(grade_a) >= TARGET_PER_COUNTRY:
                logger.info("[%s] 已找到足够 0-盾顶级节点 (%d 个)，提前完成该国筛选", country, len(grade_a))
                break
            if tested_count >= MAX_TEST_PER_COUNTRY:
                break

            tested_count += 1
            status, has_turnstile, latency, title = probe_node_chatgpt(tester_id, tag, timeout=4.0)

            if status == 200 and not has_turnstile:
                logger.info("  [%02d/%02d] [PASS: Grade A (0-盾)] 耗时: %.2fs | %s", tested_count, min(len(candidates), MAX_TEST_PER_COUNTRY), latency, tag[:50])
                grade_a.append({"tag": tag, "grade": "0-shield", "latency": latency})
            elif status == 200 and has_turnstile:
                logger.info("  [%02d/%02d] [PASS: Grade B (轻盾)] 耗时: %.2fs | %s", tested_count, min(len(candidates), MAX_TEST_PER_COUNTRY), latency, tag[:50])
                grade_b.append({"tag": tag, "grade": "1-shield", "latency": latency})
            else:
                tag_preview = tag[:50]
                logger.info("  [%02d/%02d] [FAIL: HTTP %s] 耗时: %.2fs | %s", tested_count, min(len(candidates), MAX_TEST_PER_COUNTRY), status or "Timeout", latency, tag_preview)

        # 优先选取 Grade A，不足用 Grade B 补足
        selected = grade_a + grade_b
        selected_tags = [item["tag"] for item in selected[:TARGET_PER_COUNTRY]]

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
            logger.warning("[%s] 暂未探测到 200 响应的免盾节点，建议保留全池或接入专属住宅代理", country)

    logger.info("=" * 70)
    logger.info("筛选任务全部完成！各核心国家精选节点概览：")
    for c, r in results_report.items():
        logger.info("  * [%s]: %d 个精选节点 (0-盾: %d, 轻盾: %d)", c, len(r["selected_tags"]), r["grade_a_count"], r["grade_b_count"])
        for t in r["selected_tags"]:
            logger.info("      - %s", t)
    logger.info("=" * 70)
    return results_report


if __name__ == "__main__":
    apply = "--no-apply" not in sys.argv
    run_screening(apply_to_resin=apply)
