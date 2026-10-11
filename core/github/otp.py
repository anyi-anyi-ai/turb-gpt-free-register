# -*- coding: utf-8 -*-
"""GitHub 邮件验证码 (Launch Code) 专用提取与收信监听器。"""
from __future__ import annotations

import logging
import re
import time
from typing import Optional

from core.outlook_client import OutlookAccount, _fetch_via, _http_session

logger = logging.getLogger("github.otp")


def extract_github_code_from_message(item: dict) -> str | None:
    """从原始邮件对象中提取 6-8 位 GitHub Launch Code / 验证码"""
    subject = str(item.get("subject") or "")
    body = str(item.get("body") or item.get("content") or item.get("bodyPreview") or "")
    from_info = str(item.get("from") or item.get("fromEmail") or item.get("fromName") or "")
    combined = f"{subject}\n{body}"

    # 1. 验证是否为 GitHub 相关邮件
    is_github = (
        "github" in from_info.lower() or
        "github" in subject.lower() or
        "github launch code" in combined.lower() or
        "verification code" in combined.lower()
    )
    if not is_github:
        return None

    # 2. 匹配明确语义关键字 "launch code is: 123456" 或 "verification code: 123456"
    m = re.search(
        r"(?:launch\s*code|verification\s*code|your\s*code\s*is|código\s*de\s*verificación)[:\s]*([0-9]{6,8})",
        combined,
        re.IGNORECASE
    )
    if m:
        return m.group(1).strip()

    # 3. 匹配常见 6-8 位连续数字 (排除年份 2024~2027 及特定系统端口)
    codes = re.findall(r"\b(\d{6,8})\b", combined)
    if codes:
        filtered = [
            c for c in codes
            if not (len(c) == 4 and c.startswith("20")) and c not in ("2024", "2025", "2026", "2027", "100000")
        ]
        if filtered:
            return filtered[0]

    return None


def fetch_github_launch_code(
    account: OutlookAccount,
    after_ts: float,
    max_wait: int = 180,
    poll_interval: int = 3,
) -> str | None:
    """轮询收取并提取指定 Outlook 账号在 after_ts 之后到达的 GitHub 验证码"""
    session = _http_session()
    deadline = time.time() + max_wait
    logger.info(f"📨 [收件箱监听] 正在轮询 {account.email} 提取 GitHub Launch Code (最大等待 {max_wait}s)...")

    while time.time() < deadline:
        for protocol in ("graph", "imap"):
            try:
                emails = _fetch_via(session, protocol, account)
                for item in emails:
                    code = extract_github_code_from_message(item)
                    if code:
                        logger.info(f"🎉 [收件箱监听] 提取到验证码: 【{code}】 (协议={protocol})")
                        session.close()
                        return code
            except Exception as e:
                logger.debug(f"[收件箱监听] 协议 {protocol} 异常: {e}")

        remain = int(deadline - time.time())
        if remain % 12 == 0:
            logger.info(f"⏳ [收件箱监听] 等待 GitHub 邮件中... 剩余 {remain} 秒")

        time.sleep(poll_interval)

    session.close()
    logger.warning(f"⚠️ [收件箱监听] 超时未收到来自 GitHub 的验证码 ({account.email})")
    return None
