"""
DVProxy - Admin API Routes
Account management and diagnostics
"""
import logging
from fastapi import APIRouter, HTTPException, Header, Depends, Query
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta

from app.config import settings
from app.services.accounts import get_account_manager
from app.services.logs import get_log_buffer
from app.routers.admin import get_current_admin

router = APIRouter(prefix="/v1", tags=["admin"])
logger = logging.getLogger("dvproxy.admin")


class AccountRequest(BaseModel):
    name: str
    token: str


class AccountResponse(BaseModel):
    name: str
    current: bool


@router.post("/admin/accounts/add")
async def add_account(
    req: AccountRequest,
    admin: str = Depends(get_current_admin)
):
    """Add or update an upstream account"""
    if not req.name or not req.token:
        raise HTTPException(status_code=400, detail="name and token required")
    
    mgr = get_account_manager()
    if mgr.add_account(req.name, req.token):
        return {"success": True, "name": req.name}
    raise HTTPException(status_code=400, detail="Failed to add account")


@router.get("/admin/accounts/list")
async def list_accounts(admin: str = Depends(get_current_admin)) -> List[AccountResponse]:
    """List all available accounts"""
    mgr = get_account_manager()
    accounts = mgr.list_accounts()
    return [AccountResponse(**a) for a in accounts]


@router.post("/admin/accounts/switch")
async def switch_account(
    req: AccountRequest,
    admin: str = Depends(get_current_admin)
):
    """Switch to a different account"""
    mgr = get_account_manager()
    if mgr.switch_account(req.name):
        return {"success": True, "switched_to": req.name}
    raise HTTPException(status_code=400, detail="Account not found")


@router.delete("/admin/accounts/{name}")
async def delete_account(
    name: str,
    admin: str = Depends(get_current_admin)
):
    """Delete an account"""
    mgr = get_account_manager()
    if mgr.delete_account(name):
        return {"success": True, "deleted": name}
    raise HTTPException(status_code=400, detail="Account not found")


@router.get("/admin/logs")
async def get_logs(
    since: int = Query(0, description="Get logs since this index"),
    limit: int = Query(100, description="Max logs to return"),
    admin: str = Depends(get_current_admin)
):
    """Get recent logs for real-time viewing"""
    log_buffer = get_log_buffer()
    logs, total = log_buffer.get_recent(since_index=since, limit=limit)
    return {
        "logs": logs,
        "total": total,
        "next_index": since + len(logs)
    }
