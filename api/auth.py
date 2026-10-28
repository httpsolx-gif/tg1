"""Аутентификация публичного API по хэш-токену (раздел 4 api/README.md).

Bearer-токен → поиск по sha256 (constant-time) → active/срок → IP-whitelist →
scope. Сам токен в БД не хранится. Токены выдаёт/отзывает только админ в Telegram.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, Request

from bot.services import api_tokens as T


def _client_ip(request: Request) -> Optional[str]:
    """IP клиента. За Cloudflare/nginx берём левый из X-Forwarded-For, иначе client.host.

    ВНИМАНИЕ: X-Forwarded-For можно подделать, если API смотрит в интернет напрямую.
    Поэтому наружу — ТОЛЬКО за доверенным прокси (CF/nginx), который этот заголовок
    перезаписывает (см. раздел 8 хардненинга).
    """
    cf = request.headers.get("cf-connecting-ip")
    if cf:
        return cf.strip()
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else None


def require_token(scope: str):
    """FastAPI-зависимость: валидный токен с нужным scope, иначе 401/403.

    Использование:  tok = Depends(require_token("send"))
    """
    async def dep(request: Request, authorization: Optional[str] = Header(None)):
        if not authorization or not authorization.startswith("Bearer "):
            raise HTTPException(status_code=401, detail="Unauthorized")
        raw = authorization.split(" ", 1)[1].strip()
        tok = T.lookup(raw)                      # active + не истёк, иначе None
        if tok is None:
            raise HTTPException(status_code=403, detail="Forbidden")
        ip = _client_ip(request)
        if not T.ip_allowed(tok, ip):
            raise HTTPException(status_code=403, detail="Forbidden")
        if not T.has_scope(tok, scope):
            raise HTTPException(status_code=403, detail="Forbidden")
        T.touch(tok.id, ip)
        return tok

    return dep
