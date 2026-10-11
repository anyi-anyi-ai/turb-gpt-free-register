# -*- coding: utf-8 -*-
"""Proxy Harvester 批量运行编排控制器与 Resin 流水线闭环集成。"""
from __future__ import annotations

import logging
import os
import random
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from core.proxy_harvester.email_dispatcher import CascadingEmailDispatcher
from core.proxy_harvester.models import (
    HarvestBatch,
    ProxyAccountRecord,
    init_harvester_db,
    mark_batch_synced,
    save_account_record,
    save_batch_record,
)
from core.proxy_harvester.resin_egress import ResinEgressManager
from core.proxy_harvester.registry import create_provider_instance

logger = logging.getLogger("harvester.controller")
DOWNLOADS_DIR = Path(os.getenv("DOWNLOADS_DIR", os.path.expanduser("~/Downloads")))
RESIN_PIPELINE_SCRIPT = Path("F:/github/resin/sync_custom_proxies.py")


class HarvesterController:
    """采集控制器：协调 10 账号批量抓取、防关联延迟与一键注入 Resin 粘性池。"""

    def __init__(
        self,
        target_accounts: int = 10,
        provider: str = "webshare",
        headless: bool = True,
        preferred_email: str = "auto",
        resin_platform: str = "FAST_UPSTREAM",
        auto_sync_resin: bool = True,
        output_dir: Path | str | None = None,
    ):
        self.target_accounts = target_accounts
        self.provider = provider
        self.headless = headless
        self.preferred_email = preferred_email
        self.resin_platform = resin_platform
        self.auto_sync_resin = auto_sync_resin
        self.output_dir = Path(output_dir or DOWNLOADS_DIR)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.email_dispatcher = CascadingEmailDispatcher()
        self.egress_manager = ResinEgressManager(platform=self.resin_platform)

    def run_harvest(self) -> dict[str, Any]:
        """执行完整的批量采集与入池任务。"""
        batch_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        init_harvester_db()

        logger.info("=" * 70)
        logger.info(f"🌟 开始全自动获取 100GB 纯净代理 (目标: {self.target_accounts} 个账号 = {self.target_accounts * 100} 节点)")
        logger.info(f"📁 批次标识: {batch_id} | 模式: {'无头(Headless)' if self.headless else '有头可视化'}")
        logger.info("=" * 70)

        all_collected_nodes: list[str] = []
        successful_records: list[ProxyAccountRecord] = []
        consecutive_failures = 0

        acc_idx = 1
        while len(successful_records) < self.target_accounts and consecutive_failures < 5:
            current_target_num = len(successful_records) + 1
            logger.info(f"\n▶ [{current_target_num}/{self.target_accounts}] 正在编排第 {current_target_num} 个 10GB / 100 节点账号...")

            # 1. 级联分配邮箱 (传入 provider 自动过滤已在该平台注册过的账号)
            try:
                email, source_type = self.email_dispatcher.allocate_email(
                    preferred_source=self.preferred_email,
                    provider=self.provider,
                )
            except Exception as ex:
                logger.error(f"❌ 邮箱分配失败: {ex}")
                consecutive_failures += 1
                break

            target_url = "https://dashboard.proxyscrape.com/v2/sign-up" if self.provider == "proxyscrape" else "https://dashboard.webshare.io/register"
            upstream_proxy = self.egress_manager.get_account_proxy(account_index=acc_idx, batch_tag=batch_id, target_url=target_url)

            # 3. 构造单号 Harvester 驱动 (支持轮询与指定)
            harvester = create_provider_instance(
                provider_name=self.provider,
                headless=self.headless,
                upstream_proxy=upstream_proxy,
                account_index=acc_idx,
            )

            def _get_activation_link():
                return self.email_dispatcher.wait_for_activation_link(
                    email=email,
                    source_type=source_type,
                    timeout=90,
                )

            def _get_verification_code():
                return self.email_dispatcher.wait_for_verification_code(
                    email=email,
                    source_type=source_type,
                    timeout=90,
                )

            rec = harvester.harvest_account(
                email=email,
                activation_link_getter=_get_activation_link,
                email_source=source_type,
                batch_id=batch_id,
                verification_code_getter=_get_verification_code,
            )

            if rec and rec.node_count > 0:
                consecutive_failures = 0
                successful_records.append(rec)
                all_collected_nodes.extend(rec.proxies)

                # 落盘 SQLite
                save_account_record(rec)
                self.email_dispatcher.mark_success(email, source_type)

                logger.info(f"✅ 第 {current_target_num} 个账号完成！获取 {rec.node_count} 节点 (当前累计: {len(all_collected_nodes)} 个)")

                # 防关联随机冷却 (3~7 秒)
                if len(successful_records) < self.target_accounts:
                    delay = random.uniform(3.0, 7.0)
                    logger.info(f"⏳ 防风控冷却 {delay:.1f}s 后推进下一个账号...")
                    time.sleep(delay)
            elif rec and rec.account_password:
                # 账号已成功注册/确权，但云端配额开通处于排队态
                save_account_record(rec)
                self.email_dispatcher.mark_success(email, source_type)
                logger.warning(f"⚠️ 账号 {email} 注册已成功确权，但 100 节点试用处于云端排队中。凭据已安全持久化入库，后续可直接一键登录提取！")
                consecutive_failures += 1
                time.sleep(2.0)
            else:
                consecutive_failures += 1
                self.email_dispatcher.mark_failed(email, source_type)
                logger.warning(f"⚠️ 账号 {email} 采集未通过，自动换号重试 (连续失败 {consecutive_failures}/5)")
                time.sleep(2.0)

            acc_idx += 1

        if not all_collected_nodes:
            logger.error("❌ 本次任务未能成功获取任何有效代理节点")
            return {"success": False, "total_nodes": 0, "file_path": None}

        # 4. 导出落盘标准 TXT
        out_file = self.output_dir / f"proxies_{batch_id}_{len(all_collected_nodes)}.txt"
        with open(out_file, "w", encoding="utf-8") as f:
            f.write("\n".join(all_collected_nodes) + "\n")

        logger.info("\n" + "=" * 70)
        logger.info(f"🎉 [落盘完成] 已生成标准代理文件: {out_file}")
        logger.info(f"📊 达成账号数: {len(successful_records)}/{self.target_accounts} | 节点总量: {len(all_collected_nodes)} 个")
        logger.info("=" * 70)

        batch_meta = HarvestBatch(
            batch_id=batch_id,
            total_accounts=len(successful_records),
            total_nodes=len(all_collected_nodes),
            file_path=str(out_file),
            synced_to_resin=False,
        )
        save_batch_record(batch_meta)

        # 5. 自动无缝联动触发 Resin 流水线入池
        if self.auto_sync_resin:
            self._trigger_resin_sync(out_file, batch_id)

        return {
            "success": True,
            "batch_id": batch_id,
            "total_accounts": len(successful_records),
            "total_nodes": len(all_collected_nodes),
            "file_path": str(out_file),
        }

    def _trigger_resin_sync(self, file_path: Path, batch_id: str) -> None:
        """自动拉起 Resin sync_custom_proxies.py 流水线完成测活与热更新。"""
        if not RESIN_PIPELINE_SCRIPT.exists():
            logger.warning(f"⚠️ 未找到 Resin 管道脚本: {RESIN_PIPELINE_SCRIPT}，跳过下游自动入池")
            return

        logger.info("\n🚀 正在触发 Resin 标准化入池流水线 (测活、大区分组、热更新)...")
        cmd = f'python "{RESIN_PIPELINE_SCRIPT}" "{file_path}" --yes'
        try:
            res = subprocess.run(cmd, shell=True)
            if res.returncode == 0:
                mark_batch_synced(batch_id)
                logger.info("✨ [成功] 1,000 节点已无缝并入 Resin 粘性大池！")
            else:
                logger.warning(f"⚠️ Resin 流水线返回非 0 状态码: {res.returncode}")
        except Exception as ex:
            logger.error(f"❌ 触发 Resin 流水线异常: {ex}")
