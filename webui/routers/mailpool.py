# -*- coding: utf-8 -*-
"""邮箱池与域名池路由 (Mailpool & Domain Router)。

支持 Outlook、通用 API、IMAP、UNED 以及自定义域名邮箱素材的导入、状态流转与清理。
"""
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from core import db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["mailpool"])


class MailImportPayload(BaseModel):
    source: str = "outlook"
    text: str = ""
    as_registered: bool = False
    imap_server: Optional[str] = None
    imap_port: Optional[int] = 993
    imap_ssl: bool = True


class MailStatusPayload(BaseModel):
    email: Optional[str] = None
    emails: Optional[List[str]] = None
    status: str = "available"
    source: str = "outlook"


class MailDeletePayload(BaseModel):
    email: Optional[str] = None
    emails: Optional[List[str]] = None
    source: str = "outlook"


@router.get("/api/outlook")
@router.get("/api/mailpool/list")
def api_mailpool_list(
    source: str = Query("outlook", description="邮箱来源: outlook, generic_api, imap, cloudflare_domain, uned"),
    status: Optional[str] = Query(None, description="状态"),
    q: Optional[str] = Query("", description="搜索关键字"),
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
):
    """分页拉取邮箱池素材列表。"""
    try:
        offset = (page - 1) * page_size
        fn = getattr(db, "list_email_pool_page", None)
        if fn:
            result = fn(source=source, status=status, q=q, limit=page_size, offset=offset)
            result.update({"ok": True, "page": page, "page_size": page_size})
            return result
        return {"ok": True, "items": [], "total": 0, "page": page, "page_size": page_size}
    except Exception as e:
        logger.exception("查询邮箱池失败")
        return {"ok": False, "error": str(e), "items": [], "total": 0}


@router.post("/api/outlook/import")
@router.post("/api/mailpool/import")
def api_mailpool_import(payload: MailImportPayload):
    """文本批量导入邮箱素材。"""
    source = (payload.source or "outlook").strip().lower()
    text = payload.text or ""
    records: List[Dict[str, Any]] = []

    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("----") if "----" in line else line.split("====")
        parts = [p.strip() for p in parts]
        if source == "outlook":
            if len(parts) >= 2:
                records.append({
                    "email": parts[0],
                    "password": parts[1],
                    "client_id": parts[2] if len(parts) > 2 else "",
                    "refresh_token": parts[3] if len(parts) > 3 else "",
                })
        elif source == "generic_api":
            if len(parts) >= 2:
                records.append({
                    "email": parts[0],
                    "code_url": parts[1],
                    "access_token": parts[2] if len(parts) > 2 else "",
                })
        elif source == "imap":
            if len(parts) >= 2:
                records.append({
                    "email": parts[0],
                    "password": parts[1],
                    "imap_server": payload.imap_server,
                    "imap_port": payload.imap_port,
                    "imap_ssl": payload.imap_ssl,
                })
        else:
            if len(parts) >= 1:
                records.append({"email": parts[0], "password": parts[1] if len(parts) > 1 else ""})

    if not records:
        return {"ok": False, "error": "未解析到有效邮箱数据"}

    try:
        import_fn = getattr(db, "import_email_pool", None)
        if import_fn:
            count = import_fn(source, records, as_registered=payload.as_registered)
            return {"ok": True, "count": count, "message": f"成功导入 {count} 个邮箱素材"}
        return {"ok": True, "count": len(records), "message": "模拟导入成功"}
    except Exception as e:
        logger.exception("导入邮箱失败")
        return {"ok": False, "error": str(e)}


@router.post("/api/outlook/status")
@router.post("/api/mailpool/status")
def api_mailpool_status(payload: MailStatusPayload):
    """更新邮箱状态（可用/已用/异常）。"""
    targets = payload.emails or ([payload.email] if payload.email else [])
    if not targets:
        return {"ok": False, "error": "必须提供 email 或 emails"}
    try:
        update_fn = getattr(db, "update_email_pool_status", None)
        if update_fn:
            for em in targets:
                update_fn(payload.source, em, payload.status)
        return {"ok": True, "count": len(targets)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/outlook/delete")
@router.post("/api/mailpool/delete")
def api_mailpool_delete(payload: MailDeletePayload):
    """从邮箱池中删除素材。"""
    targets = payload.emails or ([payload.email] if payload.email else [])
    if not targets:
        return {"ok": False, "error": "必须提供 email 或 emails"}
    try:
        del_fn = getattr(db, "delete_email_pool_rows", None)
        if del_fn:
            del_fn(payload.source, targets)
        return {"ok": True, "count": len(targets)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.get("/api/domain-pool")
def api_domain_pool():
    """获取域名邮箱池素材。"""
    return api_mailpool_list(source="cloudflare_domain", page=1, page_size=200)
