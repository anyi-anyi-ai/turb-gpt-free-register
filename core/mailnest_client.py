# -*- coding: utf-8 -*-
"""MailNest/迈巢临时邮箱与独占邮箱客户端。

官方文档：https://mailnest.top/docs/api-overview
支持特性：
1. 临时邮箱按项目购买（POST /api/v1/email/temporary/buy，默认 chatgpt001）
2. 独占邮箱购买（POST /api/v1/email/exclusive/buy）
3. 邮件接收与六位 OTP 提取（POST /api/v1/email/receive）
4. 注册失败/未消费时主动释放邮箱并解冻余额（POST /api/v1/email/release）
5. 账户余额查询（GET /api/v1/balance）与产品库存信息（GET /api/product/info）
"""
from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit

import requests

from config import email as _email_cfg
from core.otp_utils import extract_otp, looks_like_openai_email

logger = logging.getLogger(__name__)

DEFAULT_API_BASE = "https://mailnest.top"
REQUEST_TIMEOUT = 20
_SIX_DIGIT_RE = re.compile(r"^\d{6}$")

# MailNest 官方业务状态码含义字典
_ERROR_CODE_MESSAGES = {
    "D0001": "MailNest 账户余额不足，请前往平台充值 (mailnest.top)",
    "D0002": "MailNest 邮箱库存不足，请稍后重试或更换模式",
    "D0003": "MailNest 项目不存在或已停用",
    "D0004": "MailNest 当前无法对此邮箱执行该操作",
    "D0005": "MailNest 取件失败，请稍后再试",
    "D0006": "MailNest 账号被封禁，取件失败",
    "99999": "MailNest 服务端系统异常",
}


class MailNestClientError(RuntimeError):
    """MailNest 邮箱服务相关异常。"""


@dataclass
class MailNestAccount:
    """MailNest 邮箱上下文。"""

    email: str
    project_code: str = ""
    mode: str = "temporary"
    mailbox_id: str = ""


_CONTEXT_CACHE: dict[str, MailNestAccount] = {}
_CONTEXT_LOCK = threading.RLock()


def _cache_key(email: str) -> str:
    return str(email or "").strip().lower()


def _base_url(value: str | None = None) -> str:
    """返回规范化的 API 根地址，兼容误填 /docs 等后缀。"""
    raw = str(
        value if value is not None else getattr(_email_cfg, "MAIL_NEST_API_BASE", DEFAULT_API_BASE) or DEFAULT_API_BASE
    ).strip()
    if not raw:
        raw = DEFAULT_API_BASE
    if not re.match(r"^https?://", raw, re.IGNORECASE):
        raw = "https://" + raw

    parsed = urlsplit(raw.rstrip("/"))
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise MailNestClientError("MailNest API 地址无效，请填写 https://mailnest.top")

    path = parsed.path.rstrip("/")
    if path.lower().startswith("/docs"):
        path = ""
    return urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")


def _api_key() -> str:
    api_key = str(getattr(_email_cfg, "MAIL_NEST_API_KEY", "") or "").strip()
    if not api_key:
        raise MailNestClientError("MailNest API Key 未配置，请填写 MailNest API Key（WebUI「配置 → 邮箱 / OTP」）。")
    return api_key


def _mode() -> str:
    mode = str(getattr(_email_cfg, "MAIL_NEST_MODE", "temporary") or "temporary").strip().lower()
    if mode not in ("temporary", "exclusive"):
        logger.warning("[MailNest] 未知的 MAIL_NEST_MODE=%s，回退为 temporary", mode)
        return "temporary"
    return mode


def _project_code() -> str:
    project_code = str(getattr(_email_cfg, "MAIL_NEST_PROJECT_CODE", "chatgpt001") or "chatgpt001").strip()
    if not project_code:
        project_code = "chatgpt001"
    return project_code


def _request(
    method: str,
    path: str,
    *,
    params: dict | None = None,
    json: dict | None = None,
    authenticated: bool = True,
    timeout: int | None = None,
):
    url = _base_url() + (path if str(path).startswith("/") else f"/{path}")
    headers = {"Accept": "application/json"}
    if authenticated:
        headers["Authorization"] = f"Bearer {_api_key()}"

    req_timeout = timeout or REQUEST_TIMEOUT
    try:
        resp = requests.request(
            method.upper(),
            url,
            params=params,
            json=json,
            headers=headers,
            timeout=req_timeout,
        )
    except requests.RequestException as exc:
        raise MailNestClientError(f"MailNest 请求失败 ({path}): {type(exc).__name__}: {exc}") from exc

    if resp.status_code == 401:
        raise MailNestClientError("MailNest API Key 非法或已失效 (401 Unauthorized)")

    try:
        payload = resp.json()
    except ValueError as exc:
        raise MailNestClientError(f"MailNest 响应不是 JSON ({path}): HTTP {resp.status_code}") from exc

    if resp.status_code >= 400:
        detail = ""
        if isinstance(payload, dict):
            detail = str(payload.get("msg") or payload.get("message") or payload.get("detail") or "")
        raise MailNestClientError(f"MailNest 请求失败 ({path}): HTTP {resp.status_code}{'; ' + detail if detail else ''}")

    if not isinstance(payload, dict):
        raise MailNestClientError(f"MailNest 响应格式异常 ({path}): {payload}")

    code = str(payload.get("code") or "")
    if code != "00000":
        msg = str(payload.get("msg") or "").strip()
        custom_msg = _ERROR_CODE_MESSAGES.get(code)
        if custom_msg:
            err_text = f"{custom_msg} (code={code}{', msg=' + msg if msg else ''})"
        else:
            err_text = f"MailNest 业务错误 ({path}): code={code}, msg={msg or '未知错误'}"
        raise MailNestClientError(err_text)

    return payload.get("data")


def get_balance() -> dict:
    """查询当前账户余额信息（balance, frozen_balance, available_balance）。"""
    data = _request("GET", "/api/v1/balance")
    if not isinstance(data, dict):
        raise MailNestClientError(f"MailNest 查询余额返回数据格式异常: {data}")
    return data


def get_product_info() -> dict:
    """查询产品信息与库存（无需 API Key）。"""
    data = _request("GET", "/api/product/info", authenticated=False)
    if not isinstance(data, dict):
        raise MailNestClientError(f"MailNest 查询产品信息返回数据格式异常: {data}")
    return data


def list_projects() -> list[dict]:
    """获取所有可购买的临时邮箱项目列表。"""
    info = get_product_info()
    temporary = info.get("temporary")
    if isinstance(temporary, list):
        return temporary
    return []


def pick_account() -> MailNestAccount:
    """购买/领取一个 MailNest 邮箱并缓存上下文。支持临时模式与独占模式。"""
    mode = _mode()
    if mode == "exclusive":
        data = _request(
            "POST",
            "/api/v1/email/exclusive/buy",
            json={"count": 1},
        )
        project_code = ""
    else:
        project_code = _project_code()
        data = _request(
            "POST",
            "/api/v1/email/temporary/buy",
            json={"project_code": project_code, "count": 1},
        )

    if not isinstance(data, list) or not data:
        raise MailNestClientError(f"MailNest 购买邮箱响应缺少 data[0] (mode={mode})")

    first = data[0] or {}
    email = str(first.get("email") or "").strip()
    mailbox_id = str(first.get("id") or "").strip()

    if not email or "@" not in email:
        raise MailNestClientError(f"MailNest 购买邮箱响应缺少有效 email: {first}")

    account = MailNestAccount(
        email=email,
        project_code=project_code,
        mode=mode,
        mailbox_id=mailbox_id,
    )
    with _CONTEXT_LOCK:
        _CONTEXT_CACHE[_cache_key(email)] = account

    mode_label = "独占邮箱" if mode == "exclusive" else f"临时邮箱 (project={project_code})"
    logger.info("[MailNest] 已获取%s: %s (id=%s)", mode_label, email, mailbox_id)
    return account


def get_email() -> str:
    """兼容旧入口：返回新领取的邮箱地址。"""
    return pick_account().email


def get_account_context(email: str) -> MailNestAccount | None:
    with _CONTEXT_LOCK:
        return _CONTEXT_CACHE.get(_cache_key(email))


def release_account(email: str, status: str = "available", note: str | None = None) -> None:
    """释放邮箱上下文。若邮箱未成功消费(status!='used')，主动调用 API 解冻余额。"""
    target = str(email or "").strip()
    if not target:
        return

    with _CONTEXT_LOCK:
        _CONTEXT_CACHE.pop(_cache_key(target), None)

    # 仅在非成功使用的场景下主动调用平台释放接口，以解冻资金
    if status != "used":
        try:
            _request("POST", "/api/v1/email/release", json={"email": target})
            logger.info("[MailNest] 已成功调用平台释放接口解冻余额: %s", target)
        except Exception as exc:
            # 已成功扣费或已超时的邮箱不可释放，仅记录日志，不阻断主流程
            logger.debug("[MailNest] 释放接口响应: %s (email=%s)", exc, target)

    logger.info("[MailNest] 已释放邮箱: %s（status=%s, note=%s）", target, status, note or "")


def _get_mails(email: str):
    return _request("POST", "/api/v1/email/receive", json={"email": email})


def _timestamp(item: dict) -> float | None:
    for key in ("received_at", "timestamp", "created_at", "create_time", "date"):
        raw = item.get(key)
        if raw is None or raw == "":
            continue
        try:
            return float(raw)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).timestamp()
        except ValueError:
            pass
    return None


def _otp_item(item: dict) -> dict:
    """将 MailNest 返回的邮件对象转换为标准格式供 otp_utils 提取。"""
    body = str(item.get("body") or item.get("text") or item.get("content") or "")
    body_type = str(item.get("body_type") or "").lower()
    body_preview = str(item.get("body_preview") or "")
    is_html = body_type == "html" or "<html" in body.lower() or "<p" in body.lower() or "<div" in body.lower()

    return {
        "id": item.get("id") or item.get("mail_id"),
        "from": item.get("from_email") or item.get("from") or item.get("from_address") or item.get("sender") or "",
        "fromName": item.get("from_name") or item.get("fromName") or "",
        "subject": item.get("subject") or item.get("title") or "",
        "text": body_preview or (body if not is_html else ""),
        "html": body if is_html else (item.get("html") or item.get("html_content") or ""),
    }


def fetch_latest_otp(
    email: str,
    after_ts: float | None = None,
    max_wait: int | None = None,
    poll_interval: int | None = None,
    settle_seconds: int | None = None,
) -> str:
    """轮询 MailNest，返回领取时间后最新的 OpenAI 六位验证码。"""
    target = str(email or "").strip()
    if not target:
        raise MailNestClientError("MailNest 取码缺少邮箱地址")

    wait_seconds = int(max_wait if max_wait is not None else _email_cfg.OTP_MAX_WAIT)
    interval = max(1, int(poll_interval if poll_interval is not None else _email_cfg.OTP_POLL_INTERVAL))
    settle = max(0, int(settle_seconds if settle_seconds is not None else _email_cfg.OTP_SETTLE_SECONDS))
    deadline = time.monotonic() + max(0, wait_seconds)
    best_otp: str | None = None
    best_timestamp = float("-inf")
    settle_until: float | None = None
    last_error = "收件箱为空或尚未出现新的 OpenAI 验证码"

    logger.info("[MailNest] 开始轮询邮箱 %s，最长 %ss (after_ts=%s)", target, wait_seconds, after_ts)
    while time.monotonic() <= deadline:
        try:
            mails = _get_mails(target)
            if not isinstance(mails, list):
                raise MailNestClientError("MailNest 收件箱响应不是列表")

            # 按接收时间倒序排序
            sorted_mails = sorted(mails, key=lambda item: _timestamp(item) or float("-inf"), reverse=True)
            for mail in sorted_mails:
                if not isinstance(mail, dict):
                    continue
                message_time = _timestamp(mail)
                # 过滤掉请求前的历史旧邮件（留 30s 容差）
                if after_ts is not None and message_time is not None and message_time < after_ts - 30:
                    continue

                item = _otp_item(mail)
                raw_code = str(mail.get("code_match") or "").strip()
                otp = ""

                # 优先使用平台已匹配的验证码（若符合 6 位数字）
                if _SIX_DIGIT_RE.match(raw_code):
                    # 确保邮件具有 OpenAI 相关特征，避免非目标邮件的干扰
                    if looks_like_openai_email(item) or "chatgpt" in str(mail.get("subject", "")).lower():
                        otp = raw_code

                # 平台未能匹配或未命中时，使用本地算法深入提取
                if not otp:
                    if not looks_like_openai_email(item):
                        continue
                    otp = extract_otp(item) or ""

                if not otp or not _SIX_DIGIT_RE.match(otp):
                    continue

                candidate_time = float("-inf") if message_time is None else message_time
                if (
                    best_otp is None
                    or candidate_time > best_timestamp
                    or (candidate_time == best_timestamp and otp != best_otp)
                ):
                    best_otp = otp
                    best_timestamp = candidate_time
                    settle_until = time.monotonic() + settle
                    logger.info("[MailNest] 锁定 OTP 候选 %s，等待 %ss 确认", otp, settle)

            now = time.monotonic()
            if best_otp and settle_until is not None and now >= settle_until:
                return best_otp
        except MailNestClientError as exc:
            last_error = str(exc)
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval, remaining))

    if best_otp:
        return best_otp
    raise MailNestClientError(f"等待 MailNest 验证码超时: {target}; {last_error}")
