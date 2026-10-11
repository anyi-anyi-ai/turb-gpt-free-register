# -*- coding: utf-8 -*-
"""
UNED 终极全自动闭环挂机脚本 (Goal Runner)
流程：
1. 遍历并批量注册所有尚未注册的有效身份（自动切换独立出国出口 IP，自动识别验证码，直连接码）；
2. 遇到失败或已存在证件自动跳过并持久化标记，无缝切换下一个；
3. 注册完成后，自动调用 activator 提取 password.aspx 激活 Token 并批量激活 SSO 与邮箱；
4. 全量同步并生成 Obsidian 独立学生档案及《UNED学校邮箱汇总表.md》；
5. 输出最终核验审计报告。
"""

import sys
import time
import logging
from datetime import datetime
from pathlib import Path

# 适配 Windows 控制台编码
if sys.platform.startswith("win"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from core.uned.registrar import UnedRegistrar
from core.uned.identities import load_or_scan_identities, get_failed_identities, pick_identity
from core.uned.activator import batch_activate_all_accounts
from core.uned.obsidian_sync import sync_all_to_obsidian, update_summary_table
import core.db as db

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger("uned.goal")

def run_goal():
    print("\n" + "=" * 70)
    print("        🚀 UNED 账号全自动批量注册与激活终极流水线 (Goal Pipeline)")
    print("=" * 70)
    print(f"启动时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"目标目录: J:\\obsidian库\\ThinkDoKit-Full-1.2.0\\邮箱\\学校邮箱")
    print("=" * 70 + "\n")

    # ----------------------------------------------------
    # 阶段 1: 扫描待注册身份与环境状态
    # ----------------------------------------------------
    import sqlite3
    conn = sqlite3.connect("turb.sqlite3")
    cur = conn.cursor()
    cur.execute("SELECT doc_number FROM uned_accounts")
    registered_docs = set(r[0] for r in cur.fetchall() if r[0])
    cur.execute("SELECT count(*) FROM uned_accounts")
    initial_acc_count = cur.fetchone()[0]
    conn.close()

    failed_docs = get_failed_identities()
    all_identities = load_or_scan_identities()

    pending_ids = []
    seen = set()
    for item in all_identities:
        doc = item.get("doc_number")
        if doc and doc not in registered_docs and doc not in failed_docs and doc not in seen:
            seen.add(doc)
            pending_ids.append(item)

    print(f"[状态检测] 身份证总池: {len(all_identities)} | 已注册库: {initial_acc_count} | 失败黑名单: {len(failed_docs)} | 待注册数: {len(pending_ids)}")

    # ----------------------------------------------------
    # 阶段 2: 循环批量挂机注册剩余身份
    # ----------------------------------------------------
    if pending_ids:
        print(f"\n>>> 阶段 1/3: 开始对剩余 {len(pending_ids)} 个有效身份执行注册流水线 <<<\n")
        registrar = UnedRegistrar(debug=True)
        # 调用改进后的 batch_register，支持内部自动切换不同身份与独立IP
        registrar.batch_register(count=len(pending_ids), headless=True, interval_seconds=5)
    else:
        print("\n[阶段 1/3: 跳过] 无待注册的新身份，直接进入激活与同步阶段。")

    # ----------------------------------------------------
    # 阶段 3: 全自动 SSO 批量密码激活
    # ----------------------------------------------------
    print("\n" + "=" * 70)
    print(">>> 阶段 2/3: 启动 UNED 学生账号全量密码激活流水线 <<<")
    print("=" * 70 + "\n")
    time.sleep(3)
    activation_results = batch_activate_all_accounts(force_recheck=False)

    # ----------------------------------------------------
    # 阶段 4: Obsidian 笔记库全量刷新与汇总总表生成
    # ----------------------------------------------------
    print("\n" + "=" * 70)
    print(">>> 阶段 3/3: 全量同步数据资产至 Obsidian 知识库 <<<")
    print("=" * 70 + "\n")
    sync_all_to_obsidian()
    update_summary_table()

    # ----------------------------------------------------
    # 阶段 5: 最终核验审计
    # ----------------------------------------------------
    conn = sqlite3.connect("turb.sqlite3")
    cur = conn.cursor()
    cur.execute("SELECT status, count(*) FROM uned_accounts GROUP BY status")
    status_summary = cur.fetchall()
    cur.execute("SELECT count(*) FROM uned_accounts")
    final_total = cur.fetchone()[0]
    conn.close()

    print("\n" + "=" * 70)
    print("                 🎉 UNED 批量全自动挂机流水线全部圆满完成！")
    print("=" * 70)
    print(f"完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"注册总数: {final_total} 个账号 (新增 {final_total - initial_acc_count} 个)")
    print(f"状态汇总: {dict(status_summary)}")
    print(f"Obsidian 归档目录: J:\\obsidian库\\ThinkDoKit-Full-1.2.0\\邮箱\\学校邮箱\\")
    print("=" * 70 + "\n")

if __name__ == "__main__":
    run_goal()
