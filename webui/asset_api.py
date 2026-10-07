# -*- coding: utf-8 -*-
"""Flask 资产 API 蓝图 (挂载 reg-factory 标准 /api/assets/* 路由)。"""
from __future__ import annotations

import hmac
import json
import logging
import os
from flask import Blueprint, request, jsonify, Response

from common import asset_store

logger = logging.getLogger(__name__)
assets_bp = Blueprint("assets_bp", __name__, url_prefix="/api/assets")


def _asset_denied():
    expected_key = os.environ.get("REG_FACTORY_ASSET_API_KEY", "").strip()
    if expected_key:
        header_key = request.headers.get("X-API-Key", "").strip()
        auth_header = request.headers.get("Authorization", "").strip()
        bearer_key = auth_header[7:].strip() if auth_header.lower().startswith("bearer ") else ""
        provided = header_key or bearer_key
        if not provided or not hmac.compare_digest(provided, expected_key):
            return jsonify({"ok": False, "error": "unauthorized asset access"}), 401
        return None

    remote_addr = (request.remote_addr or "").strip()
    if remote_addr in ("127.0.0.1", "::1", "localhost", "testclient", ""):
        logger.warning("未配置 REG_FACTORY_ASSET_API_KEY，资产 API 处于本机免密模式 (若使用反向隧道存在被绕过风险，建议在 .env 中配置)")
        return None
    return jsonify({"ok": False, "error": "未配置 REG_FACTORY_ASSET_API_KEY 时，资产 API 仅允许本机访问"}), 403


@assets_bp.get("/summary")
def api_asset_summary():
    denied = _asset_denied()
    if denied:
        return denied
    try:
        data = asset_store.summary()
        return jsonify({"ok": True, "data": data})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@assets_bp.get("/emails")
def api_asset_emails():
    denied = _asset_denied()
    if denied:
        return denied
    index_param = request.args.get("index")
    index = int(index_param) if index_param is not None and index_param.isdigit() else None
    fmt = request.args.get("format", "json").lower()
    email_provider = request.args.get("email_provider", "")
    pristine_only = request.args.get("pristine_only", "false").lower() in ("1", "true", "yes")
    normal_only = request.args.get("normal_only", "false").lower() in ("1", "true", "yes")
    no_graph_only = request.args.get("no_graph_only", "false").lower() in ("1", "true", "yes")
    status = request.args.get("status", "")

    try:
        claimed = asset_store.get_email(
            index=index,
            output_format=fmt,
            claim_once=True,
            email_provider=email_provider,
            pristine_only=pristine_only,
            no_graph_only=no_graph_only,
            verified_only=normal_only and not no_graph_only and not bool(str(status).strip()),
            status=status,
        )
        if fmt == "line":
            return Response(claimed.get("line", ""), mimetype="text/plain; charset=utf-8")
        return jsonify({"ok": True, "data": claimed} if isinstance(claimed, dict) and "ok" not in claimed else claimed)
    except asset_store.AssetError as exc:
        return jsonify({"ok": False, "error": str(exc)}), exc.status_code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@assets_bp.get("/cookies/<platform>")
def api_asset_cookies(platform: str):
    denied = _asset_denied()
    if denied:
        return denied
    fmt = request.args.get("format", "raw").lower()
    index_param = request.args.get("index")
    index = int(index_param) if index_param is not None and index_param.isdigit() else None
    codex_phone_status = request.args.get("codex_phone_status", "")
    email_provider = request.args.get("email_provider", "")
    status = request.args.get("status", "")

    try:
        claimed = asset_store.get_platform_asset(
            platform=platform,
            output_format=fmt,
            index=index,
            claim_once=True,
            codex_phone_status=codex_phone_status,
            email_provider=email_provider,
            status=status,
        )
        if fmt in ("cookies", "cpa", "sub2api") and isinstance(claimed.get("content"), (dict, list)):
            return jsonify(claimed["content"])
        if fmt == "cpa" and isinstance(claimed.get("content"), str):
            return Response(claimed["content"], mimetype="text/plain; charset=utf-8")
        return jsonify({"ok": True, "data": claimed} if isinstance(claimed, dict) and "ok" not in claimed else claimed)
    except asset_store.AssetError as exc:
        return jsonify({"ok": False, "error": str(exc)}), exc.status_code
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@assets_bp.post("/cursors/reset")
def api_asset_cursors_reset():
    denied = _asset_denied()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    scope = str(data.get("scope") or "all").lower()
    try:
        reset_info = asset_store.reset_cursor(scope=scope)
        return jsonify({"ok": True, "data": reset_info})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400


@assets_bp.post("/export")
def api_asset_export():
    denied = _asset_denied()
    if denied:
        return denied
    data = request.get_json(silent=True) or {}
    target = str(data.get("target") or "all").lower()
    try:
        exported = asset_store.export_batch(target=target)
        return jsonify({"ok": True, "data": exported})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
