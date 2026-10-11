# -*- coding: utf-8 -*-
"""
Grok 已注册账号（status='already_registered'）自动化登录与凭据恢复工具。

特性：
1. 采用 Email sign-in code 免密验证码方式登录 x.ai / Grok，无需猜测历史密码；
2. 自动化穿透 Cloudflare Turnstile 人机验证并提取 SSO 会话凭证；
3. 原生拉起官方 OAuth Device Flow，完成授权并获取持久 Access / Refresh Token；
4. 遇到账号封禁（Suspended/Disabled）精准标记为 status='banned' 并跳过，平滑处理下一个；
5. 多国代理节点（US, CA, NL, DE, GB 等）轮换与连通性自检，避免单节点风控；
6. 2路并发安全运行，实时汇总进度；全部结束后自动同步至下游 gptGrok2api。
"""
from __future__ import annotations

import argparse
import itertools
import os
import queue
import sqlite3
import sys
import threading
import time
from pathlib import Path

# 确保 UTF-8 编码与无缓冲实时打印
try:
    sys.stdout.reconfigure(encoding="utf-8", line_buffering=True, errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", line_buffering=True, errors="replace")
except Exception:
    pass

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.grok.registration_service import recover_single_grok_account
from core.gptgrok2api_sync import push_grok_accounts_to_gptgrok2api
from core import db

DB_PATH = _ROOT / "turb.sqlite3"


def sync_all_healthy_grok_accounts():
    """将数据库中所有 healthy 的 Grok 账号全量推送到下游。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        cur.execute("SELECT * FROM grok_accounts WHERE archived = 0 AND status = 'healthy'")
        rows = cur.fetchall()
        accounts = [dict(r) for r in rows]
        if accounts:
            print(f"[Sync] 正在将 {len(accounts)} 个健康 Grok 账号推送到下游 gptGrok2api ...")
            res = push_grok_accounts_to_gptgrok2api(accounts)
            print(f"[Sync] 下游同步结果: {res}")
            return res
        else:
            print("[Sync] 暂无健康账号可供推送")
            return {"ok": True, "count": 0}
    finally:
        conn.close()


def get_pending_accounts(limit: int = 0) -> list[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        cur = conn.cursor()
        sql = "SELECT id, email FROM grok_accounts WHERE status = 'already_registered' ORDER BY id ASC"
        if limit > 0:
            sql += f" LIMIT {limit}"
        cur.execute(sql)
        rows = cur.fetchall()
        return [{"id": r["id"], "email": r["email"]} for r in rows]
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="批量恢复已注册 Grok 账号")
    parser.add_argument("--concurrency", "-c", type=int, default=2, help="并发 Worker 数量（默认 2）")
    parser.add_argument("--limit", "-l", type=int, default=0, help="最多处理账号数量（0 表示全量）")
    parser.add_argument("--headless", action="store_true", default=True, help="无头模式运行")
    parser.add_argument("--no-headless", action="store_false", dest="headless", help="有头可视化运行")
    parser.add_argument("--countries", type=str, default="US,CA,NL,DE,GB", help="代理国家轮换列表")
    args = parser.parse_args()

    pending = get_pending_accounts(args.limit)
    total = len(pending)
    print("=" * 60)
    print(f"[*] 开始执行 Grok 已注册账号自动化批量恢复任务")
    print(f"待处理账号数: {total}")
    print(f"并发 Worker: {args.concurrency}")
    print(f"代理轮换国家: {args.countries}")
    print(f"无头模式: {args.headless}")
    print("=" * 60)

    if total == 0:
        print("[!] 没有发现 status = 'already_registered' 的账号，退出。")
        return

    country_list = [c.strip().upper() for c in args.countries.split(",") if c.strip()]
    if not country_list:
        country_list = ["US", "CA", "NL"]

    task_queue = queue.Queue()
    for item in pending:
        task_queue.put(item)

    lock = threading.Lock()
    stats = {
        "total": total,
        "processed": 0,
        "success": 0,
        "banned": 0,
        "failed": 0,
    }

    country_cycle = itertools.cycle(country_list)

    def worker_loop(worker_id: int):
        while not task_queue.empty():
            try:
                task = task_queue.get_nowait()
            except queue.Empty:
                break

            email = task["email"]
            with lock:
                target_country = next(country_cycle)

            prefix = f"[W-{worker_id}]"
            print(f"\n{prefix} 开始恢复账号: {email} (国家节点: {target_country}) ...")

            def worker_log(msg: str):
                print(f"{prefix} {msg}")

            start_t = time.time()
            try:
                res = recover_single_grok_account(
                    email=email,
                    country=target_country,
                    run_quality_probe=True,
                    enable_device_oauth=True,
                    headless=args.headless,
                    log_callback=worker_log,
                )
                duration = time.time() - start_t
                status = res.get("status", "unknown")

                with lock:
                    stats["processed"] += 1
                    if res.get("success"):
                        stats["success"] += 1
                        print(f"{prefix} [SUCCESS] 恢复成功: {email} (耗时: {duration:.1f}s, status={status})")
                    elif status == "banned":
                        stats["banned"] += 1
                        print(f"{prefix} [BANNED] 账号被封禁: {email} (耗时: {duration:.1f}s, 原因: {res.get('error')})")
                    else:
                        stats["failed"] += 1
                        print(f"{prefix} [FAILED] 恢复失败: {email} (耗时: {duration:.1f}s, 原因: {res.get('error')})")

                    done = stats["processed"]
                    succ = stats["success"]
                    bann = stats["banned"]
                    fail = stats["failed"]
                    print(f"[进度] {done}/{total} ({(done/total*100):.1f}%) | 成功: {succ} | 封禁: {bann} | 失败: {fail}")

            except Exception as e:
                duration = time.time() - start_t
                with lock:
                    stats["processed"] += 1
                    stats["failed"] += 1
                    print(f"{prefix} [EXCEPTION] 异常: {email} (耗时: {duration:.1f}s, err={e})")

            finally:
                task_queue.task_done()
                time.sleep(2)  # 账号间轻微间隔

    threads = []
    for wid in range(1, args.concurrency + 1):
        t = threading.Thread(target=worker_loop, args=(wid,), daemon=True, name=f"recover-worker-{wid}")
        t.start()
        threads.append(t)
        time.sleep(1.5)  # 启动错峰

    for t in threads:
        t.join()

    print("\n" + "=" * 60)
    print("[*] 所有账号恢复任务处理完毕！")
    print(f"总计: {stats['total']} | 成功: {stats['success']} | 封禁: {stats['banned']} | 失败: {stats['failed']}")
    print("=" * 60)

    # 触发下游同步
    if stats["success"] > 0:
        try:
            sync_all_healthy_grok_accounts()
        except Exception as se:
            print(f"[!] 下游同步出现异常: {se}")

    print("\n[Done] 批量恢复流程结束。")



if __name__ == "__main__":
    main()
