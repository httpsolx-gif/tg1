from fastapi import APIRouter, Depends, Query, HTTPException, Header
from typing import Optional
from common.config import settings

router = APIRouter(prefix="/api/logs", tags=["logs"])

def auth(authorization: Optional[str] = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Unauthorized")
    token = authorization.split(" ", 1)[1]
    if token != settings.API_LOG_AUTH_TOKEN:
        raise HTTPException(403, "Forbidden")
    return True

@router.get("/errors")
def list_errors(_: bool = Depends(auth), limit: int = Query(50, ge=1, le=500)):
    # TODO: read from DB or file
    return {"items": [], "limit": limit}

@router.get("/sends")
def list_sends(_: bool = Depends(auth), limit: int = Query(50, ge=1, le=500)):
    # TODO: read from DB send_logs
    return {"items": [], "limit": limit}
