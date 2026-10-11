# -*- coding: utf-8 -*-
"""
UNED 学生邮箱 ChatGPT 自动化批量注册流水线 (UNED ChatGPT Registration Pipeline)

核心架构与安全风控：
1. 严选针对 ChatGPT 0 盾（Grade A）实测通过的低风控代理池（涵盖 US, DE, JP, CH, RO, GB, NL, FR + 本地高可用代理）；
2. 逐一轮换独立干净出口 IP，每注册一个账号平滑轮换到下一个国家/节点，严控单 IP 请求频次；
3. 自动解析 UNED 学生档案（清洗合规 ASCII 字母名、自动转换 YYYY-MM-DD 生日）；
4. 深度打通微软 Graph API 接码链路，UNED 学生邮箱自动转发至个人 Outlook 邮箱，秒级抓取 OpenAI 6位 OTP 验证码；
5. 注册成功后将 ChatGPT 凭据（Token / Session / 密码 / 代理出口）写入 SQLite accounts 表，并更新 uned_accounts 标记；
6. 支持单账号测试模式（--canary）、批量挂机模式（--count N）、断点续跑等。
"""
import argparse
import io
import json
import logging
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

# 适配 Windows 控制台编码
if sys.platform == "win32":
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from dotenv import load_dotenv
load_dotenv(_ROOT / ".env", override=True)

import sqlite3
from core.outlook_client import get_account_context, _CONTEXT_CACHE
from core.email_provider import resolve_email_source, wait_for_otp
from core.profile_utils import generate_random_birthday
from core.name_samples import random_display_name

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("uned.chatgpt")

DB_PATH = _ROOT / "turb.sqlite3"
CLEAN_NODES_PATH = _ROOT / "data" / "clean_nodes.json"
RESIN_PROXY_TOKEN = os.getenv("RESIN_PROXY_TOKEN", "resin_proxy_c3d7b8e1f0a2496582d9183b")
RESIN_HOST_PORT = "127.0.0.1:2260"

DEFAULT_CLEAN_ROUTES = [
    {"country": "US", "proxy": f"http://US:{RESIN_PROXY_TOKEN}@{RESIN_HOST_PORT}"},
    {"country": "DE", "proxy": f"http://DE:{RESIN_PROXY_TOKEN}@{RESIN_HOST_PORT}"},
    {"country": "JP", "proxy": f"http://JP:{RESIN_PROXY_TOKEN}@{RESIN_HOST_PORT}"},
    {"country": "CH", "proxy": f"http://CH:{RESIN_PROXY_TOKEN}@{RESIN_HOST_PORT}"},
    {"country": "RO", "proxy": f"http://RO:{RESIN_PROXY_TOKEN}@{RESIN_HOST_PORT}"},
    {"country": "GB", "proxy": f"http://GB:{RESIN_PROXY_TOKEN}@{RESIN_HOST_PORT}"},
    {"country": "NL", "proxy": f"http://NL:{RESIN_PROXY_TOKEN}@{RESIN_HOST_PORT}"},
    {"country": "FR", "proxy": f"http://FR:{RESIN_PROXY_TOKEN}@{RESIN_HOST_PORT}"},
    {"country": "AUTO", "proxy": "http://127.0.0.1:7897"},
]


def load_clean_proxies() -> list[dict]:
    """从 clean_nodes.json 读取已筛选出的高质量 0-盾平台配置，若无则使用默认池。"""
    if CLEAN_NODES_PATH.exists():
        try:
            with open(CLEAN_NODES_PATH, "r", encoding="utf-8") as f:
                report = json.load(f)
            from core.ip_quota_manager import ip_quota_manager
            active_routes = []
            for country, data in report.items():
                if data.get("selected_tags"):
                    p_url = ip_quota_manager.get_registration_proxy(country=country) or f"http://{country}:{RESIN_PROXY_TOKEN}@{RESIN_HOST_PORT}"
                    active_routes.append({
                        "country": country,
                        "proxy": p_url,
                    })
            if active_routes:
                # 附加上本地高可用代理
                active_routes.append({"country": "AUTO", "proxy": "http://127.0.0.1:7897"})
                logger.info("[代理池] 成功从 clean_nodes.json 加载 %d 个经过 0 盾探测的出口平台 (绑定粘性 Session)", len(active_routes))
                return active_routes
        except Exception as e:
            logger.warning("[代理池] 读取 clean_nodes.json 异常: %s，回退默认代理池", e)
    return DEFAULT_CLEAN_ROUTES


def format_birth_date(raw_date: str) -> str:
    """转换西班牙 DD/MM/YYYY 格式为 OpenAI 所需的 YYYY-MM-DD。"""
    try:
        parts = raw_date.strip().split("/")
        if len(parts) == 3:
            day, month, year = parts
            return f"{year}-{int(month):02d}-{int(day):02d}"
    except Exception:
        pass
    return generate_random_birthday()


def clean_student_name(full_name: str) -> str:
    """清洗显示名，确保只含有英文字母和空格，符合 OpenAI 接口约束。"""
    cleaned = re.sub(r'[^a-zA-Z\s]', '', full_name or "").strip()
    cleaned = re.sub(r'\s+', ' ', cleaned)
    if len(cleaned) < 3:
        return random_display_name()
    return cleaned


def get_pending_uned_accounts(limit: int = 100, start_id: int = 0) -> list[dict]:
    """获取所有已激活、已开通转发且尚未在 accounts 表中注册过 ChatGPT 的 UNED 账号。"""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    query = """
    SELECT u.id, u.uned_identifier, u.email, u.full_name, u.birth_date, u.payload_json
    FROM uned_accounts u
    WHERE u.forwarding_enabled = 1
      AND u.id >= ?
      AND NOT EXISTS (
          SELECT 1 FROM accounts a
          WHERE LOWER(a.email) = LOWER(u.uned_identifier || '@alumno.uned.es')
      )
    ORDER BY u.id ASC
    LIMIT ?
    """
    cur.execute(query, (start_id, limit))
    rows = cur.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append({
            "id": r[0],
            "uned_identifier": r[1],
            "student_email": f"{r[1]}@alumno.uned.es",
            "forward_outlook": r[2],
            "full_name": clean_student_name(r[3]),
            "birth_date": format_birth_date(r[4]),
            "payload_json": json.loads(r[5]) if r[5] else {},
        })
    return results


def mark_uned_account_registered(uned_id: int, student_email: str, account_id: int):
    """更新 SQLite 中 uned_accounts 表的注册标记。"""
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("SELECT payload_json FROM uned_accounts WHERE id = ?", (uned_id,))
        row = cur.fetchone()
        payload = json.loads(row[0]) if row and row[0] else {}
        payload["chatgpt_registered"] = True
        payload["chatgpt_registered_at"] = datetime.now().isoformat()
        payload["chatgpt_account_id"] = account_id
        cur.execute(
            "UPDATE uned_accounts SET payload_json = ?, updated_at = ? WHERE id = ?",
            (json.dumps(payload, ensure_ascii=False), datetime.now().isoformat(), uned_id)
        )
        cur.execute(
            "UPDATE email_pool SET status = 'used', updated_at = ? WHERE source = 'uned' AND LOWER(email) = LOWER(?)",
            (datetime.now().isoformat(), student_email)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.warning("[DB] 更新 uned_accounts / email_pool 标记失败 (ID %s): %s", uned_id, e)


def register_single_student(acc_info: dict, route_info: dict, headless: bool = False, driver_mode: str = "cloak") -> dict:
    """使用指定独立代理为单个 UNED 学生邮箱执行 ChatGPT 注册。"""
    student_email = acc_info["student_email"]
    forward_email = acc_info["forward_outlook"]
    name = acc_info["full_name"]
    birthday = acc_info["birth_date"]
    proxy = route_info["proxy"]
    country = route_info["country"]

    logger.info("=" * 70)
    logger.info("🎯 开始注册学生账号: %s", student_email)
    logger.info("   学生姓名: %s | 生日: %s", name, birthday)
    logger.info("   转发邮箱: %s (自动接码)", forward_email)
    logger.info("   出口地区: %s | 代理: %s", country, proxy)
    logger.info("   驱动模式: %s | 无头模式: %s", driver_mode, headless)
    logger.info("=" * 70)

    # 1. 预热与绑定 Outlook 接码上下文
    fwd_context = get_account_context(forward_email)
    if not fwd_context:
        raise RuntimeError(f"无法为转发邮箱 {forward_email} 获取 Microsoft Graph 凭据！")
    _CONTEXT_CACHE[student_email.lower()] = fwd_context

    # 2. 执行注册驱动
    if driver_mode == "cloak":
        from config import cloakbrowser as _cloak_cfg
        # 动态控制无头模式与窗口生命周期
        _cloak_cfg.CLOAK_HEADLESS = headless
        _cloak_cfg.CLOAK_KEEP_BROWSER_OPEN = False

        from core.cloakbrowser_registration import run_cloak_registration
        result = run_cloak_registration(
            email=student_email,
            name=name,
            birthday=birthday,
            proxy=proxy,
            country=country if country != "AUTO" else None,
        )
    else:
        # 协议模式
        from config import roxybrowser as _roxy_cfg
        _roxy_cfg.REGISTRATION_DRIVER = "protocol"
        from main import run_registration
        result = run_registration(
            email=student_email,
            name=name,
            birthday=birthday,
            proxy=proxy,
            country=country if country != "AUTO" else None,
        )

    return result


def is_account_healthy(forward_email: str) -> bool:
    """快速检查绑定的个人邮箱是否曾收到微软锁定警报或已被封禁，若是则提前跳过避免等待。"""
    try:
        from core.outlook_client import get_account_context, _fetch_via_graph_direct
        acc = get_account_context(forward_email)
        if not acc:
            return True
        msgs = _fetch_via_graph_direct(acc)
        for m in msgs:
            subj = str(m.get("subject") or "").lower()
            if "bloqueada" in subj or "cuenta bloqueada" in subj or "access deactivated" in subj:
                return False
        return True
    except Exception:
        return True


def check_route_liveness(proxy_url: str, timeout: float = 4.5) -> bool:
    """使用 Chrome 真实指纹秒级探测该代理能否 0-盾直达 ChatGPT。"""
    try:
        from curl_cffi import requests as c_requests
        resp = c_requests.get("https://chatgpt.com", proxy=proxy_url, timeout=timeout, impersonate="chrome120")
        return resp.status_code == 200
    except Exception:
        return False


def run_pipeline(count: int = 1, start_id: int = 0, canary: bool = False, headless: bool = False, driver: str = "cloak", delay: int = 8):
    """运行批量注册流水线，逐一轮换独立干净出口 IP，持续执行直到达到目标成功数。"""
    proxies = load_clean_proxies()
    target_success = 1 if canary else count

    # 加载候选池（加载较多候选，确保剔除异常账号后仍能达成目标成功数）
    pending_accounts = get_pending_uned_accounts(limit=max(100, target_success * 3), start_id=start_id)
    if not pending_accounts:
        logger.info("✅ 未找到待注册的 UNED 学生账号（可能已全量注册完成）。")
        return

    logger.info("=" * 70)
    logger.info("🚀 启动 UNED 学生邮箱 ChatGPT 批量注册流水线")
    logger.info("目标达成数量: %d 个成功 | 待选候选池: %d 个 | 纯净代理池: %d 个节点", target_success, len(pending_accounts), len(proxies))
    logger.info("安全风控策略: 0 盾过滤 + IP 逐一轮换 + 邮箱健康前置校验 + 智能人类行为模拟")
    logger.info("=" * 70)

    success_count = 0
    fail_count = 0
    attempt_count = 0

    for acc in pending_accounts:
        if success_count >= target_success:
            logger.info("🎯 已圆满达成目标注册成功数: %d 个！提前退出流水线。", success_count)
            break

        # 快速健康检查
        if not is_account_healthy(acc["forward_outlook"]):
            logger.warning("⚠️ [跳过] 账号 %s (%s) 曾收到微软锁定警报，跳过并执行下一个", acc["student_email"], acc["forward_outlook"])
            continue

        # 严格筛选当前 ChatGPT 200 活体代理节点
        route = None
        for _ in range(len(proxies)):
            candidate = proxies[attempt_count % len(proxies)]
            attempt_count += 1
            if check_route_liveness(candidate["proxy"]):
                route = candidate
                break
            else:
                logger.warning("⚠️ [代理跳过] 节点 [%s] 遇盾拦截/暂不可达，自动轮换下一节点...", candidate["country"])
        if not route:
            route = proxies[attempt_count % len(proxies)]

        logger.info("\n>>> [尝试 #%d | 当前成功 %d/%d] 轮换到出口节点: [%s] <<<", attempt_count, success_count, target_success, route["country"])

        t0 = time.time()
        try:
            res = register_single_student(acc, route, headless=headless, driver_mode=driver)
            if res and res.get("success"):
                success_count += 1
                acc_id = res.get("account_id") or 0
                mark_uned_account_registered(acc["id"], acc["student_email"], acc_id)
                logger.info("🎉 [注册成功 %d/%d] %s (耗时: %.1fs, 账号ID: %s)", success_count, target_success, acc["student_email"], time.time() - t0, acc_id)
            else:
                fail_count += 1
                logger.warning("❌ [注册未成功] %s: %s", acc["student_email"], res.get("error") if res else "未知错误")
        except Exception as e:
            fail_count += 1
            logger.error("💥 [注册异常] %s: %s", acc["student_email"], e)

        if success_count < target_success and delay > 0:
            logger.info("⏳ 严格风控冷却，等待 %d 秒后轮换至下一个 IP...", delay)
            time.sleep(delay)

    logger.info("\n" + "=" * 70)
    logger.info("📊 UNED 学生邮箱注册执行汇总:")
    logger.info("   总尝试: %d | 成功: %d | 失败: %d | 目标: %d", attempt_count, success_count, fail_count, target_success)
    logger.info("=" * 70)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="UNED 学生邮箱 ChatGPT 自动化批量注册流水线")
    parser.add_argument("--count", type=int, default=1, help="注册账号数量 (默认 1)")
    parser.add_argument("--start-id", type=int, default=0, help="起始 UNED 账号 ID")
    parser.add_argument("--canary", action="store_true", help="单账号金丝雀冒烟测试 (跑 1 个账号)")
    parser.add_argument("--headless", action="store_true", help="无头模式运行浏览器 (默认显示窗口便于观察)")
    parser.add_argument("--driver", default="cloak", choices=["cloak", "protocol"], help="注册驱动 (默认 cloak)")
    parser.add_argument("--delay", type=int, default=8, help="账号之间等待延迟秒数 (默认 8s)")
    args = parser.parse_args()

    run_pipeline(
        count=args.count,
        start_id=args.start_id,
        canary=args.canary,
        headless=args.headless,
        driver=args.driver,
        delay=args.delay,
    )
