# -*- coding: utf-8 -*-
"""Grok (x.ai) OAuth Device Code 授权与凭据交换模块。

基于 x.ai 官方 OAuth2 Device Authorization Grant (RFC 8628) 规范，
将网页注册提取的会话在同一浏览器中转化为官方长效 OAuth 凭据（Refresh Token + Access Token），
并导出符合 CLIProxyAPI (CPA) 及 Grok2API 标准的 JSON 授权记录。
"""
from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime, timezone
from typing import Any, Callable

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    curl_requests = None

logger = logging.getLogger(__name__)

# x.ai 官方 Client ID（对齐 CLIProxyAPI / grok-build-auth）
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
OIDC_ISSUER = "https://auth.x.ai"
SCOPES = "openid profile email offline_access grok-cli:access api:access"

DEVICE_CODE_URL = f"{OIDC_ISSUER}/oauth2/device/code"
TOKEN_ENDPOINT = f"{OIDC_ISSUER}/oauth2/token"
DEVICE_CODE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEVICE_DEFAULT_INTERVAL = 5
DEVICE_DEFAULT_EXPIRES = 1800
DEVICE_POLL_CAP_SECONDS = 90

GROK_VERSION = "0.2.93"
GROK_TOKEN_UA = f"grok-pager/{GROK_VERSION} grok-shell/{GROK_VERSION} (linux; x86_64)"
DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

CPA_GROK_BASE_URL = "https://cli-chat-proxy.grok.com/v1"
CPA_GROK_HEADERS = {
    "User-Agent": GROK_TOKEN_UA,
    "X-XAI-Token-Auth": "xai-grok-cli",
    "x-authenticateresponse": "authenticate-response",
    "x-grok-client-identifier": "grok-pager",
    "x-grok-client-version": GROK_VERSION,
}


def b64url_decode(seg: str) -> bytes:
    """Base64 URL 安全解码"""
    seg = seg.strip()
    seg += "=" * (-len(seg) % 4)
    return base64.urlsafe_b64decode(seg)


def decode_jwt_payload(token: str) -> dict[str, Any]:
    """解析 JWT Payload 字典"""
    if not token or "." not in token:
        return {}
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    try:
        raw = b64url_decode(parts[1])
        return json.loads(raw.decode("utf-8", errors="ignore"))
    except Exception:
        return {}


def iso_utc_from_unix(ts: float | int | None = None) -> str:
    """Unix 时间戳转换为 ISO 8601 UTC 字符串"""
    if ts is None:
        ts = time.time()
    dt = datetime.fromtimestamp(float(ts), tz=timezone.utc)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def request_device_code(
    proxy: str = "",
    log_callback: Callable[[str], None] | None = None,
) -> dict[str, Any] | None:
    """申请 OAuth device_code 与 user_code。

    返回值包含:
      - device_code: 轮询 Token 的凭据
      - user_code: 用户确认码 (如 ABCD-1234)
      - open_url: 浏览器需打开的授权完整 URL
      - interval: 轮询间隔秒数 (默认 5)
      - expires_in: 授权码有效秒数
    """
    def log(msg: str):
        if log_callback:
            log_callback(f"[DeviceAuth] {msg}")
        logger.info(f"[DeviceAuth] {msg}")

    log("正在向 x.ai 申请 Device Code ...")
    session = None
    if curl_requests:
        session = curl_requests.Session(impersonate="chrome124")
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}
    else:
        import requests
        session = requests.Session()
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}

    try:
        resp = session.post(
            DEVICE_CODE_URL,
            data={"client_id": CLIENT_ID, "scope": SCOPES},
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Accept": "application/json",
                "User-Agent": DEFAULT_BROWSER_UA,
            },
            timeout=20,
        )
        if resp.status_code != 200:
            log(f"申请 Device Code 失败: HTTP {resp.status_code} - {resp.text[:180]}")
            return None

        data = resp.json()
        device_code = str(data.get("device_code") or "").strip()
        user_code = str(data.get("user_code") or "").strip()
        if not device_code or not user_code:
            log(f"Device Code 响应缺失核心字段: {data}")
            return None

        interval = max(1, int(data.get("interval") or DEVICE_DEFAULT_INTERVAL))
        expires_in = max(30, int(data.get("expires_in") or DEVICE_DEFAULT_EXPIRES))
        verification_complete = str(
            data.get("verification_uri_complete")
            or data.get("verification_url_complete")
            or ""
        ).strip()
        verification_uri = str(
            data.get("verification_uri") or data.get("verification_url") or ""
        ).strip()

        open_url = verification_complete or (
            f"{verification_uri}?user_code={urllib.parse.quote(user_code)}"
            if verification_uri
            else f"https://accounts.x.ai/oauth2/device?user_code={urllib.parse.quote(user_code)}"
        )

        log(f"成功获取 Device Code: user_code={user_code}, 有效期={expires_in}s")
        return {
            "device_code": device_code,
            "user_code": user_code,
            "interval": interval,
            "expires_in": expires_in,
            "verification_uri": verification_uri,
            "open_url": open_url,
        }
    except Exception as exc:
        log(f"申请 Device Code 网络异常: {exc}")
        return None
    finally:
        try:
            session.close()
        except Exception:
            pass


def poll_device_token(
    device_code: str,
    interval: int = DEVICE_DEFAULT_INTERVAL,
    expires_in: int = DEVICE_DEFAULT_EXPIRES,
    proxy: str = "",
    log_callback: Callable[[str], None] | None = None,
    grace_invalid_grant: float = 60.0,
    stop_checker: Callable[[], bool] | None = None,
) -> dict[str, Any] | None:
    """轮询 device_code 换取 OAuth access_token 与 refresh_token。

    grace_invalid_grant: 浏览器刚刚提交「允许」时服务端可能落库有微弱延迟，
    在宽限期内遇到 invalid_grant 当作 pending 继续重试，避免过早报错中断。
    """
    def log(msg: str):
        if log_callback:
            log_callback(f"[DeviceAuth] {msg}")
        logger.info(f"[DeviceAuth] {msg}")

    device_code = str(device_code or "").strip()
    if not device_code:
        log("device_code 为空，无法轮询")
        return None

    session = None
    if curl_requests:
        session = curl_requests.Session(impersonate="chrome124")
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}
    else:
        import requests
        session = requests.Session()
        if proxy:
            session.proxies = {"http": proxy, "https": proxy}

    try:
        poll_interval = max(1, int(interval or DEVICE_DEFAULT_INTERVAL))
        started_at = time.time()
        deadline = started_at + min(expires_in, DEVICE_POLL_CAP_SECONDS)
        grace_deadline = started_at + max(0.0, float(grace_invalid_grant or 0.0))

        log("开始轮询换取 Access / Refresh Token ...")
        last_error = ""

        while time.time() < deadline:
            if stop_checker and stop_checker():
                log("检测到外部停止信号，终止 Token 轮询")
                return None

            try:
                resp = session.post(
                    TOKEN_ENDPOINT,
                    data={
                        "grant_type": DEVICE_CODE_GRANT_TYPE,
                        "device_code": device_code,
                        "client_id": CLIENT_ID,
                    },
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "application/json",
                        "User-Agent": GROK_TOKEN_UA,
                        "X-Grok-Client-Version": GROK_VERSION,
                    },
                    timeout=20,
                )
            except Exception as exc:
                last_error = f"网络抖动: {exc}"
                time.sleep(poll_interval)
                continue

            try:
                payload = resp.json()
            except Exception:
                payload = {}

            err = str(payload.get("error") or "").strip()

            # 成功换得 Token
            if resp.status_code == 200 and payload.get("access_token"):
                access_token = str(payload.get("access_token"))
                refresh_token = str(payload.get("refresh_token") or "")
                exp_in = int(payload.get("expires_in") or 3600)
                log(f"成功换取 OAuth Token! (access_token前缀: {access_token[:15]}..., 有效期: {exp_in}s)")
                return {
                    "access_token": access_token,
                    "refresh_token": refresh_token,
                    "id_token": str(payload.get("id_token") or ""),
                    "token_type": str(payload.get("token_type") or "Bearer"),
                    "expires_in": exp_in,
                    "scope": str(payload.get("scope") or SCOPES),
                }

            if err == "authorization_pending":
                time.sleep(poll_interval)
                continue

            if err == "slow_down":
                poll_interval = min(poll_interval + 5, 25)
                time.sleep(poll_interval)
                continue

            if err == "invalid_grant" and time.time() < grace_deadline:
                # 宽限期内继续等待服务端授权落库
                time.sleep(poll_interval)
                continue

            if err in ("expired_token", "access_denied", "invalid_grant"):
                desc = str(payload.get("error_description") or "").strip()
                log(f"Token 轮询终态异常: {err} ({desc})")
                return None

            last_error = f"HTTP {resp.status_code}: {err or resp.text[:120]}"
            time.sleep(poll_interval)

        log(f"Token 轮询超时: {last_error}")
        return None
    finally:
        try:
            session.close()
        except Exception:
            pass


def token_to_cpa_record(token: dict[str, Any], email: str = "", sso: str = "") -> dict[str, Any]:
    """将 OAuth Token 转换为 CLIProxyAPI (CPA) 扁平 xai auth 规范字典。

    对齐 CPA internal/auth/xai/token.go 的 TokenStorage 结构。
    """
    access = str(token.get("access_token") or "")
    refresh = str(token.get("refresh_token") or "")
    id_token = str(token.get("id_token") or "")

    access_claims = decode_jwt_payload(access)
    id_claims = decode_jwt_payload(id_token) if id_token else {}

    acc_email = email or id_claims.get("email") or access_claims.get("email") or ""
    sub = access_claims.get("sub") or id_claims.get("sub") or ""

    expired_iso = ""
    if "exp" in access_claims:
        expired_iso = iso_utc_from_unix(access_claims["exp"])
    elif token.get("expires_in"):
        try:
            expired_iso = iso_utc_from_unix(time.time() + int(token["expires_in"]))
        except Exception:
            pass

    record = {
        "type": "xai",
        "auth_kind": "oauth",
        "email": acc_email,
        "sub": sub,
        "access_token": access,
        "refresh_token": refresh,
        "id_token": id_token,
        "token_type": token.get("token_type", "Bearer"),
        "expires_in": token.get("expires_in", 3600),
        "expired": expired_iso,
        "last_refresh": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "redirect_uri": "http://127.0.0.1:56121/callback",
        "token_endpoint": TOKEN_ENDPOINT,
        "base_url": CPA_GROK_BASE_URL,
        "disabled": False,
        "headers": dict(CPA_GROK_HEADERS),
    }
    if sso:
        record["sso"] = sso.strip()
    return record


def token_to_grok2api_account(token: dict[str, Any], email: str = "", sso: str = "") -> dict[str, Any]:
    """生成适用于 Grok2API 导入的 Build 账号数据包。"""
    cpa = token_to_cpa_record(token, email=email, sso=sso)
    return {
        "email": cpa.get("email"),
        "access_token": cpa.get("access_token"),
        "refresh_token": cpa.get("refresh_token"),
        "sso": sso,
        "source": "turb-grok-register",
        "created_at": cpa.get("last_refresh"),
    }
