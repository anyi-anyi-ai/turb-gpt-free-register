# -*- coding: utf-8 -*-
"""Proxy Harvester 三级级联邮箱调度器 (Outlook -> UNED -> CF Temp Email)。"""
from __future__ import annotations

import json
import logging
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from core.cf_temp_mail_client import (
    create_address,
    list_messages,
    get_message_detail,
    _message_id,
    _otp_item,
    CFTempMailAccount,
)
from core.outlook_client import (
    get_account_context,
    _fetch_via,
    _http_session,
    _parse_email_ts,
    _is_after,
)

logger = logging.getLogger("harvester.email")
DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "turb.sqlite3"


class CascadingEmailDispatcher:
    """三级级联邮箱管理器：自动调度 Outlook、UNED 及 CF 域名邮箱，并提取激活链接。"""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path or DEFAULT_DB_PATH)
        self._cf_cache: dict[str, CFTempMailAccount] = {}
        self._allocated_emails: set[str] = set()

    def _collect_excluded(
        self,
        provider: str | None = None,
        excluded_emails: set[str] | list[str] | None = None,
    ) -> set[str]:
        all_excluded = set(self._allocated_emails)
        if excluded_emails:
            for em in excluded_emails:
                if em:
                    all_excluded.add(em.strip().lower())
        if provider:
            # 自动查询 traffic_assets.sqlite3 的 provider_registered_emails
            harvester_db = Path("F:/github/traffic-harvester/traffic_assets.sqlite3")
            if harvester_db.exists():
                try:
                    with sqlite3.connect(harvester_db) as conn:
                        rows = conn.execute(
                            "SELECT email FROM provider_registered_emails WHERE lower(provider) = ?",
                            (provider.lower().strip(),),
                        ).fetchall()
                        for r in rows:
                            if r[0]:
                                all_excluded.add(r[0].strip().lower())
                except Exception as ex:
                    logger.debug(f"[EmailDispatcher] 读取已注册库略过: {ex}")

        # 排除本工程历史已完成采集的账号
        if self.db_path.exists():
            try:
                with sqlite3.connect(self.db_path) as conn:
                    rows = conn.execute("SELECT email FROM harvested_proxy_accounts").fetchall()
                    for r in rows:
                        if r[0]:
                            all_excluded.add(r[0].strip().lower())
            except Exception:
                pass
        return all_excluded

    def allocate_email(
        self,
        preferred_source: str = "auto",
        provider: str | None = None,
        excluded_emails: set[str] | list[str] | None = None,
    ) -> tuple[str, str]:
        """按三级级联策略分配可用邮箱：
        
        1. 优先使用微软邮箱 (Outlook / Hotmail，108+ 具备 Graph API OAuth 凭据)
        2. 顺延尝试西班牙学生邮箱 (UNED @alumno.uned.es，92+ 激活)
        3. 最后使用域名邮箱 (Cloudflare Worker 域名邮箱托底保底，无限动态生成)
        
        Returns:
            (email_address, source_type) 其中 source_type 为 'outlook' | 'uned' | 'cf_custom'
        """
        total_excluded = self._collect_excluded(provider=provider, excluded_emails=excluded_emails)

        # Tier-1: 优先使用微软邮箱 (Outlook / Hotmail)
        if preferred_source in ("auto", "outlook"):
            outlook_email = self._try_allocate_outlook(total_excluded) or self._try_reuse_outlook(total_excluded)
            if outlook_email:
                self._allocated_emails.add(outlook_email.lower())
                logger.info(f"📧 [邮箱调度] 成功分配 Tier-1 微软 Outlook 邮箱: {outlook_email}")
                return outlook_email, "outlook"
            if preferred_source == "outlook":
                raise RuntimeError("Outlook 现货池与复用池已无可用账号")

        # Tier-2: 顺延尝试西班牙学生邮箱 (UNED)
        if preferred_source in ("auto", "uned"):
            uned_email = self._try_allocate_uned(total_excluded)
            if uned_email:
                self._allocated_emails.add(uned_email.lower())
                logger.info(f"📧 [邮箱调度] 成功分配 Tier-2 西班牙学生邮箱: {uned_email}")
                return uned_email, "uned"
            if preferred_source == "uned":
                raise RuntimeError("UNED 学生邮箱池已无可用账号")

        # Tier-3: 最后使用自建 Cloudflare Worker 域名邮箱保底
        try:
            cf_acc = create_address()
            self._cf_cache[cf_acc.email.lower()] = cf_acc
            self._allocated_emails.add(cf_acc.email.lower())
            logger.info(f"📧 [邮箱调度] 成功分配 Tier-3 自建域名邮箱: {cf_acc.email}")
            return cf_acc.email, "cf_custom"
        except Exception as e:
            logger.error(f"❌ [邮箱调度] CF 域名邮箱生成异常: {e}")
            raise

    def _try_allocate_outlook(self, excluded_set: set[str] | None = None) -> str | None:
        """从 email_pool 提取未使用的 Outlook 邮箱。"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # 自动解冻超过 10 分钟未完成的孤儿 allocated 状态邮箱
                conn.execute("UPDATE email_pool SET status = 'available' WHERE status = 'allocated' AND updated_at < datetime('now', '-10 minutes')")
                conn.commit()

                ex = list(excluded_set or self._allocated_emails)
                placeholders = ",".join("?" for _ in ex) if ex else "''"
                query = f"""
                    SELECT email FROM email_pool
                    WHERE source = 'outlook' AND status = 'available' AND archived = 0
                    AND lower(email) NOT IN ({placeholders})
                    ORDER BY id ASC LIMIT 1;
                """
                row = conn.execute(query, ex).fetchone()
                if row and row[0]:
                    email = row[0]
                    conn.execute(
                        "UPDATE email_pool SET status = 'allocated', updated_at = datetime('now') WHERE email = ?",
                        (email,),
                    )
                    conn.commit()
                    return email
        except Exception as e:
            logger.warning(f"[EmailDispatcher] 提取 Outlook 异常: {e}")
        return None

    def _try_reuse_outlook(self, excluded_set: set[str] | None = None) -> str | None:
        """从 email_pool 挑选带有有效 OAuth 上下文的 Outlook 邮箱供代理商注册复用。"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                params: list[str] = list(excluded_set or self._allocated_emails)
                placeholders = ",".join("?" for _ in params) if params else "''"
                query = f"""
                    SELECT email FROM email_pool
                    WHERE source = 'outlook' AND archived = 0 AND status = 'used'
                    AND lower(email) NOT IN ({placeholders})
                    ORDER BY id ASC LIMIT 1;
                """
                row = conn.execute(query, params).fetchone()
                if row and row[0]:
                    return row[0]
        except Exception as e:
            logger.warning(f"[EmailDispatcher] 提取复用 Outlook 异常: {e}")
        return None

    def _try_allocate_uned(self, excluded_set: set[str] | None = None) -> str | None:
        """从 uned_accounts 提取可接收转发的 UNED 邮箱。"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                ex = list(excluded_set or self._allocated_emails)
                placeholders = ",".join("?" for _ in ex) if ex else "''"
                query = f"""
                    SELECT uned_identifier, email FROM uned_accounts
                    WHERE status = 'activated'
                    AND lower(email) NOT IN ({placeholders})
                    AND lower(uned_identifier || '@alumno.uned.es') NOT IN ({placeholders})
                    ORDER BY RANDOM() LIMIT 1;
                """
                params = ex + ex if ex else []
                row = conn.execute(query, params).fetchone()
                if row and row[0]:
                    ident = str(row[0]).strip().lower()
                    return f"{ident}@alumno.uned.es"
        except Exception as e:
            logger.warning(f"[EmailDispatcher] 提取 UNED 异常: {e}")
        return None

    def wait_for_activation_link(
        self,
        email: str,
        source_type: str,
        timeout: int = 75,
        poll_interval: int = 4,
        link_pattern: str = r'https?://[^\s<>"\'\)]+(?:verify[^\s<>"\'\)]*|confirm[^\s<>"\'\)]*|activate[^\s<>"\'\)]*)',
    ) -> str | None:
        """统一监听并提取目标邮箱收到的注册激活链接。"""
        logger.info(f"⏳ 正在监听 [{source_type}] {email} 的验证邮件 (超时 {timeout}s)...")
        start_ts = time.time()

        if source_type == "cf_custom":
            cf_acc = self._cf_cache.get(email.lower())
            if not cf_acc or not cf_acc.jwt:
                logger.error(f"[EmailDispatcher] 缺失 CF 临时邮箱 JWT: {email}")
                return None
            deadline = start_ts + timeout
            while time.time() < deadline:
                try:
                    msgs = list_messages(cf_acc.jwt)
                    for item in msgs:
                        detail = item
                        msg_id = _message_id(item)
                        if msg_id:
                            detail = get_message_detail(cf_acc.jwt, msg_id)
                        text_val = str(detail.get("text") or detail.get("body") or detail.get("html") or "")
                        matches = re.findall(link_pattern, text_val, re.IGNORECASE)
                        if matches:
                            clean_links = [m.replace("&amp;", "&") for m in matches if "unsubscribe" not in m.lower()]
                            if clean_links:
                                act_url = clean_links[0]
                                logger.info(f"🔗 [EmailDispatcher] CF 临时邮箱捕获激活链接: {act_url}")
                                return act_url
                except Exception as ex:
                    logger.debug(f"[EmailDispatcher] CF 轮询波动: {ex}")
                time.sleep(poll_interval)
            return None

        # Outlook 或 UNED (经由转发到达对应 Outlook)
        account_ctx = get_account_context(email)
        if not account_ctx:
            logger.error(f"[EmailDispatcher] 未能解析到 {email} 的凭证上下文")
            return None

        deadline = start_ts + timeout
        session = _http_session()

        while time.time() < deadline:
            all_candidates = []
            for protocol in ("graph", "imap"):
                try:
                    msgs = _fetch_via(session, protocol, account_ctx)
                    for item in msgs:
                        ts = _parse_email_ts(item) or 0.0
                        all_candidates.append((item, ts))
                    if msgs and protocol == "graph":
                        break
                except Exception as ex:
                    logger.debug(f"[EmailDispatcher] {protocol} 抓取波动: {ex}")

            all_candidates.sort(key=lambda x: x[1], reverse=True)

            for item, ts in all_candidates:
                # 优先匹配最近 30 分钟内收到的目标激活邮件 (防止重试注册或发信延迟被误杀)
                if ts and ts < (start_ts - 1800):
                    continue

                subject = str(item.get("subject") or "").lower()
                body_txt = str(item.get("bodyPreview") or item.get("bodyText") or item.get("text") or "")
                body_html = str(item.get("bodyHtml") or item.get("content") or item.get("html") or item.get("body") or "")
                full_content = f"{subject}\n{body_txt}\n{body_html}"

                matches = re.findall(link_pattern, full_content, re.IGNORECASE)
                if matches:
                    clean_links = [m.replace("&amp;", "&") for m in matches if "unsubscribe" not in m.lower()]
                    if clean_links:
                        act_url = clean_links[0]
                        logger.info(f"🔗 [EmailDispatcher] 捕获到激活链接: {act_url}")
                        return act_url

            time.sleep(poll_interval)

        logger.warning(f"⚠️ [EmailDispatcher] 等待 {email} 邮件激活链接超时 ({timeout}s)")
        return None

    def wait_for_verification_code(
        self,
        email: str,
        source_type: str,
        timeout: int = 75,
        poll_interval: int = 3,
        code_pattern: str = r"(?:verification code|code is|code:)\s*[:：]?\s*([a-zA-Z0-9]{6,16})",
    ) -> str | None:
        """统一监听并提取目标邮箱收到的注册验证码。"""
        logger.info(f"⏳ 正在监听 [{source_type}] {email} 的验证码邮件 (超时 {timeout}s)...")
        start_ts = time.time()
        deadline = start_ts + timeout

        if source_type == "cf_custom":
            cf_acc = self._cf_cache.get(email.lower())
            if not cf_acc or not cf_acc.jwt:
                logger.error(f"[EmailDispatcher] 缺失 CF 临时邮箱 JWT: {email}")
                return None
            while time.time() < deadline:
                try:
                    msgs = list_messages(cf_acc.jwt)
                    for item in msgs:
                        detail = item
                        msg_id = _message_id(item)
                        if msg_id:
                            detail = get_message_detail(cf_acc.jwt, msg_id)
                        text_val = str(detail.get("text") or detail.get("body") or detail.get("html") or "")
                        m = re.search(code_pattern, text_val, re.IGNORECASE)
                        if m:
                            code = m.group(1).strip()
                            logger.info(f"🔢 [EmailDispatcher] 捕获到验证码: {code}")
                            return code
                except Exception as ex:
                    logger.debug(f"[EmailDispatcher] CF 验证码轮询波动: {ex}")
                time.sleep(poll_interval)
            return None

        # Outlook 或 UNED
        account_ctx = get_account_context(email)
        if not account_ctx:
            logger.error(f"[EmailDispatcher] 未能解析到 {email} 的凭证上下文")
            return None

        session = _http_session()
        while time.time() < deadline:
            all_candidates = []
            for protocol in ("graph", "imap"):
                try:
                    msgs = _fetch_via(session, protocol, account_ctx)
                    for item in msgs:
                        ts = _parse_email_ts(item) or 0.0
                        all_candidates.append((item, ts))
                    if msgs and protocol == "graph":
                        break
                except Exception as ex:
                    logger.debug(f"[EmailDispatcher] {protocol} 抓取波动: {ex}")

            all_candidates.sort(key=lambda x: x[1], reverse=True)
            for item, ts in all_candidates:
                if ts and ts < (start_ts - 1800):
                    continue
                subject = str(item.get("subject") or "")
                body_txt = str(item.get("bodyPreview") or item.get("bodyText") or item.get("text") or "")
                body_html = str(item.get("bodyHtml") or item.get("content") or item.get("html") or item.get("body") or "")
                full_content = f"{subject}\n{body_txt}\n{body_html}"
                m = re.search(code_pattern, full_content, re.IGNORECASE)
                if m:
                    code = m.group(1).strip()
                    logger.info(f"🔢 [EmailDispatcher] 捕获到验证码: {code}")
                    return code

            time.sleep(poll_interval)

        logger.warning(f"⚠️ [EmailDispatcher] 等待 {email} 邮件验证码超时 ({timeout}s)")
        return None

    def mark_success(self, email: str, source_type: str) -> None:
        """标记邮箱已成功使用。"""
        if source_type == "outlook":
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "UPDATE email_pool SET status = 'used', updated_at = datetime('now') WHERE email = ?",
                        (email,),
                    )
                    conn.commit()
            except Exception:
                pass

    def mark_failed(self, email: str, source_type: str) -> None:
        """标记邮箱不可用或回滚状态。"""
        self._allocated_emails.add(email.lower())
        if source_type == "outlook":
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "UPDATE email_pool SET status = 'available', updated_at = datetime('now') WHERE email = ?",
                        (email,),
                    )
                    conn.commit()
            except Exception:
                pass
        elif source_type == "uned":
            ident = email.split("@")[0].strip().lower()
            try:
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute(
                        "UPDATE uned_accounts SET status = 'failed' WHERE LOWER(uned_identifier) = ?",
                        (ident,),
                    )
                    conn.commit()
            except Exception:
                pass
