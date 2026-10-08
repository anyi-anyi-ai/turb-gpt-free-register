# -*- coding: utf-8 -*-
"""资产深度维护生命周期路由 (Asset Maintenance Router)。

支持 ChatGPT 测活、套餐查询、提链、Codex/Sub2API 同步以及 Grok SSO 探针巡检与网关同步。
"""
import logging
from typing import Any, Dict, List, Optional
from fastapi import APIRouter
from pydantic import BaseModel

from core import db

logger = logging.getLogger(__name__)
router = APIRouter(tags=["maintenance"])


class BulkAccountIdsPayload(BaseModel):
    account_ids: Optional[List[int]] = None
    ids: Optional[List[int]] = None
    force: bool = False
    profile_id: Optional[str] = None
    proxy: Optional[str] = None


@router.post("/api/maintenance/chatgpt/live-check")
@router.post("/api/accounts/check-live-bulk")
def api_chatgpt_live_check_bulk(payload: BulkAccountIdsPayload):
    """ChatGPT 账号批量查活。"""
    raw_ids = payload.account_ids or payload.ids or []
    if not raw_ids:
        return {"ok": False, "error": "account_ids 不能为空"}

    try:
        from core import live_check_service
        account_ids = [int(i) for i in raw_ids]
        queued = []
        skipped = []
        for acc_id in account_ids:
            task = live_check_service.submit_live_check(acc_id, force=payload.force)
            if task.get("ok"):
                queued.append({"id": acc_id})
            else:
                skipped.append({"id": acc_id, "reason": task.get("error", "无法提交")})
        return {
            "ok": True,
            "queued": queued,
            "queued_count": len(queued),
            "skipped": skipped,
            "message": f"成功提交 {len(queued)} 个查活任务",
        }
    except Exception as e:
        logger.exception("批量查活失败")
        return {"ok": False, "error": str(e)}


@router.post("/api/maintenance/chatgpt/plan-check")
@router.post("/api/accounts/check-plan-bulk")
def api_chatgpt_plan_check_bulk(payload: BulkAccountIdsPayload):
    """ChatGPT 账号批量套餐/Plus 状态巡检。"""
    raw_ids = payload.account_ids or payload.ids or []
    if not raw_ids:
        return {"ok": False, "error": "account_ids 不能为空"}

    try:
        from core import plan_check_service
        account_ids = [int(i) for i in raw_ids]
        queued = []
        skipped = []
        for acc_id in account_ids:
            task = plan_check_service.submit_plan_check(acc_id, force=payload.force)
            if task.get("ok"):
                queued.append({"id": acc_id})
            else:
                skipped.append({"id": acc_id, "reason": task.get("error", "无法提交")})
        return {
            "ok": True,
            "queued": queued,
            "queued_count": len(queued),
            "skipped": skipped,
            "message": f"成功提交 {len(queued)} 个套餐查询任务",
        }
    except Exception as e:
        logger.exception("批量套餐查询失败")
        return {"ok": False, "error": str(e)}


@router.post("/api/maintenance/chatgpt/extract-link")
@router.post("/api/accounts/extract-link-bulk")
def api_chatgpt_extract_link_bulk(payload: BulkAccountIdsPayload):
    """ChatGPT 账号批量提取兑换/邀请链接。"""
    raw_ids = payload.account_ids or payload.ids or []
    if not raw_ids:
        return {"ok": False, "error": "account_ids 不能为空"}

    try:
        from core import extract_link_service
        account_ids = [int(i) for i in raw_ids]
        queued = []
        for acc_id in account_ids:
            extract_link_service.submit_extract_link(acc_id)
            queued.append({"id": acc_id})
        return {"ok": True, "queued": queued, "count": len(queued)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


@router.post("/api/maintenance/grok/probe-inspect")
@router.post("/api/grok/accounts/probe-inspect")
def api_grok_probe_inspect(payload: BulkAccountIdsPayload):
    """Grok 账号 SSO 探针巡检。"""
    raw_ids = payload.account_ids or payload.ids or []
    try:
        from core.grok.quality_checker import GrokQualityChecker

        if raw_ids:
            accounts = db.get_grok_accounts_by_ids([int(i) for i in raw_ids])
        else:
            res = db.query_grok_accounts(page=1, page_size=200)
            accounts = res.get("items") or []

        if not accounts:
            return {"ok": False, "error": "没有可供巡检的 Grok 账号"}

        inspected = []
        counts = {"healthy": 0, "degraded": 0, "bot_flagged": 0, "error": 0}

        for acc in accounts:
            acc_id = int(acc.get("id") or 0)
            email = str(acc.get("email") or "")
            sso = str(acc.get("sso_token") or acc.get("sso_rw") or "")
            checker = GrokQualityChecker(proxy=payload.proxy or "", timeout_sec=15)
            try:
                res = checker.check(sso_token=sso, run_probe=True, profile_id=payload.profile_id)
                st = res.status if res.status in counts else "error"
                counts[st] += 1
                if acc_id and hasattr(db, "update_grok_account_status"):
                    db.update_grok_account_status(
                        acc_id,
                        status=res.status,
                        bot_flag_source=res.bot_flag_source,
                        bot_flag_details=res.bot_flag_details,
                        probe_tps=res.probe_tps,
                        probe_reasoning_tokens=res.probe_reasoning_tokens,
                    )
                inspected.append({
                    "id": acc_id,
                    "email": email,
                    "status": res.status,
                    "tps": res.probe_tps,
                })
            except Exception as exc:
                counts["error"] += 1
                inspected.append({"id": acc_id, "email": email, "status": "error", "error": str(exc)})

        return {
            "ok": True,
            "total": len(accounts),
            "counts": counts,
            "results": inspected,
        }
    except Exception as e:
        logger.exception("Grok 探针巡检失败")
        return {"ok": False, "error": str(e)}


@router.post("/api/maintenance/grok/push-gptgrok2api")
@router.post("/api/grok/accounts/push-gptgrok2api")
def api_grok_push_gptgrok2api(payload: BulkAccountIdsPayload):
    """批量同步 Grok 账号到 GPTGrok2API 网关。"""
    raw_ids = payload.account_ids or payload.ids or []
    try:
        from core import grok_gptgrok2api_sync
        if raw_ids:
            accounts = db.get_grok_accounts_by_ids([int(i) for i in raw_ids])
        else:
            accounts = db.get_unsynced_grok_accounts(limit=100) if hasattr(db, "get_unsynced_grok_accounts") else []

        if not accounts:
            return {"ok": False, "error": "没有可同步的账号"}

        synced = 0
        for acc in accounts:
            if hasattr(grok_gptgrok2api_sync, "sync_account"):
                ok = grok_gptgrok2api_sync.sync_account(acc)
                if ok:
                    synced += 1

        return {"ok": True, "total": len(accounts), "synced": synced}
    except Exception as e:
        return {"ok": False, "error": str(e)}
