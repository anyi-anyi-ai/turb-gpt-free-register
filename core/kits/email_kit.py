# -*- coding: utf-8 -*-
"""
core/kits/email_kit.py — 统一邮箱能力积木组件 (EmailKit)
提供开箱即用的：
  - 跨平台邮箱池自动分配与排重 (pick_account)
  - Outlook Graph API 毫秒级轮询与链接抽取 (fetch_verification_link)
  - 邮箱验证码（OTP/PinCode）正则抽取 (fetch_verification_code)
  - 邮箱池健康度自检与可用库存统计 (get_pool_stats)
"""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

from core.flows.pool_helper import pick_unregistered_email_for_platform
from core.outlook_client import get_account_context, _http_session, _fetch_via
from core.db.base import _LOCK, _sqlite_conn

logger = logging.getLogger("turb.kit.email")


class EmailKit:
    """标准化邮箱服务原子积木。"""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = db_path

    def pick_account(
        self,
        platform_table: str,
        specific_email: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        从邮箱池中分配指定平台尚未注册过的可用账号。
        """
        return pick_unregistered_email_for_platform(
            platform_table=platform_table,
            specific_email=specific_email,
            db_path=self.db_path,
        )

    def fetch_verification_link(
        self,
        email: str,
        url_patterns: Optional[List[str]] = None,
        keywords: Optional[List[str]] = None,
        max_wait: int = 70,
        poll_interval: int = 3,
    ) -> str:
        """
        通过 Outlook Graph API 轮询收件箱并抽取激活链接。
        :param email: 目标邮箱
        :param url_patterns: 优先匹配的正则规则列表
        :param keywords: 备选链接必须包含的关键字列表
        :param max_wait: 最大等待超时秒数
        :param poll_interval: 轮询间隔秒数
        """
        account = get_account_context(email)
        if account is None:
            raise RuntimeError(f"未找到 {email} 的账号上下文（请确认 refresh_token 是否存在）")

        deadline = time.time() + max_wait
        session = _http_session()

        logger.info(f"[EmailKit] 正在轮询 {email} 的验证邮件，最长等待 {max_wait}s...")

        while time.time() < deadline:
            try:
                emails = _fetch_via(session, "graph", account)
                for item in emails:
                    body_txt = str(item.get("bodyText") or item.get("body") or item.get("content") or item.get("bodyPreview") or "")
                    body_html = str(item.get("bodyHtml") or item.get("html") or item.get("body") or "")
                    full_body = f"{body_txt}\n{body_html}"

                    # 提取所有 http(s) 链接
                    links = [l.replace("&amp;", "&") for l in re.findall(r'https?://[^\s"\'<>]+', full_body)]

                    # 1. 优先使用自定义正则匹配
                    if url_patterns:
                        for pattern in url_patterns:
                            reg = re.compile(pattern, re.IGNORECASE)
                            for link in links:
                                if reg.search(link):
                                    logger.info(f"[EmailKit] 成功通过正则规则提取到激活链接: {link[:80]}...")
                                    return link

                    # 2. 备选关键字匹配
                    if keywords:
                        for link in links:
                            if any(k.lower() in link.lower() for k in keywords):
                                logger.info(f"[EmailKit] 成功通过关键字提取到激活链接: {link[:80]}...")
                                return link
            except Exception as e:
                logger.debug(f"[EmailKit] 单次拉取邮件出现非致命错误: {e}")

            time.sleep(poll_interval)

        raise TimeoutError(f"等待 {email} 的验证邮件超时（>{max_wait}s）")

    def fetch_verification_code(
        self,
        email: str,
        code_pattern: str = r"\b(\d{6})\b",
        subject_keywords: Optional[List[str]] = None,
        max_wait: int = 70,
        poll_interval: int = 3,
    ) -> str:
        """
        通过 Outlook Graph API 轮询收件箱并抽取数字验证码 (OTP)。
        :param email: 目标邮箱
        :param code_pattern: 匹配验证码的正则表达式 (默认 6 位纯数字)
        :param subject_keywords: 过滤主题/发件人关键字
        :param max_wait: 最大等待超时秒数
        :param poll_interval: 轮询间隔秒数
        """
        account = get_account_context(email)
        if account is None:
            raise RuntimeError(f"未找到 {email} 的账号上下文（请确认 refresh_token 是否存在）")

        deadline = time.time() + max_wait
        session = _http_session()
        reg = re.compile(code_pattern)

        logger.info(f"[EmailKit] 正在轮询 {email} 的验证码邮件，最长等待 {max_wait}s...")

        while time.time() < deadline:
            try:
                emails = _fetch_via(session, "graph", account)
                for item in emails:
                    subject = str(item.get("subject") or "")
                    if subject_keywords:
                        if not any(k.lower() in subject.lower() for k in subject_keywords):
                            continue

                    body_txt = str(item.get("bodyText") or item.get("body") or item.get("content") or item.get("bodyPreview") or "")
                    body_html = str(item.get("bodyHtml") or item.get("html") or item.get("body") or "")
                    full_text = f"{subject}\n{body_txt}\n{body_html}"

                    match = reg.search(full_text)
                    if match:
                        code = match.group(1) if match.groups() else match.group(0)
                        logger.info(f"[EmailKit] 成功从邮件中提取到验证码: {code}")
                        return code.strip()
            except Exception as e:
                logger.debug(f"[EmailKit] 轮询验证码出现非致命错误: {e}")

            time.sleep(poll_interval)

        raise TimeoutError(f"等待 {email} 的验证码邮件超时（>{max_wait}s）")

    def get_pool_stats(self, platform_table: Optional[str] = None) -> Dict[str, int]:
        """查询邮箱池总体库存与指定平台的未注册余量。"""
        with _LOCK, _sqlite_conn() as conn:
            total_emails = conn.execute("SELECT COUNT(1) FROM email_pool WHERE status = 'active';").fetchone()[0]
            unregistered = 0
            if platform_table:
                try:
                    query = f"""
                        SELECT COUNT(1)
                        FROM email_pool ep
                        WHERE ep.status = 'active'
                          AND LOWER(ep.email) NOT IN (
                              SELECT LOWER(email) FROM {platform_table} WHERE email IS NOT NULL
                          );
                    """
                    unregistered = conn.execute(query).fetchone()[0]
                except Exception:
                    unregistered = total_emails

            return {
                "total_active_emails": total_emails,
                "unregistered_count": unregistered,
            }

    @staticmethod
    def audit_email_domain(email: str) -> bool:
        """
        DNS / 拓扑前置探测：验证邮箱域名是否存在合规解析，
        杜绝因死域名/无效临时域名导致的平台直接风控拦截。
        """
        if not email or "@" not in email:
            return False
        domain = email.split("@")[-1].strip().lower()
        if not domain:
            return False
        known_good = {
            "outlook.com", "hotmail.com", "live.com", "msn.com",
            "gmail.com", "yahoo.com", "icloud.com", "uned.es",
        }
        if domain in known_good:
            return True
        import socket
        try:
            socket.gethostbyname(domain)
            return True
        except Exception:
            return False

