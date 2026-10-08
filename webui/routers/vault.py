# -*- coding: utf-8 -*-
"""通用资产金库路由 (Universal Vault Router)。"""
from typing import Optional
from fastapi import APIRouter, Query, Response
from pydantic import BaseModel

from core import vault_service

router = APIRouter(prefix="/api/vault", tags=["vault"])


@router.get("/accounts")
def api_vault_list_accounts(
    platform: Optional[str] = Query(None, description="平台标识: chatgpt, grok, suno 等"),
    query: Optional[str] = Query(None, description="搜索关键词"),
    status: Optional[str] = Query(None, description="状态过滤"),
    page: int = Query(1, ge=1, description="页码"),
    page_size: int = Query(50, ge=1, le=500, description="每页数量"),
):
    """跨平台通用账号列表分页查询（默认字段脱敏）。"""
    return vault_service.list_accounts(
        platform=platform,
        query=query,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.get("/metrics")
def api_vault_metrics():
    """全库账号资产与健康度概览统计。"""
    return vault_service.get_vault_metrics()


@router.get("/{platform}/{account_id}/secret")
def api_vault_account_secret(
    platform: str,
    account_id: str,
    field: str = Query("all", description="具体机密字段: password, access_token, totp_secret 等，或 all"),
):
    """安全按需读取账号机密凭据。"""
    return vault_service.get_account_secret(platform=platform, account_id=account_id, field=field)


@router.delete("/{platform}/{account_id}")
def api_vault_delete_account(platform: str, account_id: str):
    """从数据库中删除指定账号。"""
    return vault_service.delete_account(platform=platform, account_id=account_id)


@router.get("/export")
def api_vault_export(
    platform: Optional[str] = Query(None, description="平台标识，留空为全部"),
    format_type: str = Query("sub2api", description="导出格式: sub2api, openai, csv, txt"),
):
    """按指定规范格式导出账号资产 (Sub2API / OpenAI / CSV / TXT)。"""
    res = vault_service.export_accounts(platform=platform, format_type=format_type)
    if not res.get("ok"):
        return res

    filename = res.get("filename", "export.txt")
    mimetype = res.get("mimetype", "text/plain")
    content = res.get("content", "")

    return Response(
        content=content.encode("utf-8"),
        media_type=mimetype,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate",
        },
    )
