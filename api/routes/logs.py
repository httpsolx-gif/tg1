"""Логи через API — за хэш-токеном со scope `logs:read` (раздел 5 api/README.md).

Раньше здесь был ОДИН статичный пароль (settings.API_LOG_AUTH_TOKEN) со сравнением
`!=`, который отдал бы email получателей (PII) кому угодно с этим паролем. Заменено
на обычный хэш-токен + отдельный scope. Тела пока пустые (TODO Фаза 2): когда
включим, отдавать МИНИМУМ полей и только данные владельца токена, без полных адресов.
"""
from fastapi import APIRouter, Depends, Query

from api.auth import require_token

router = APIRouter(prefix="/api/logs", tags=["logs"])

# один scope на обе ручки; сам токен доступен как аргумент, если понадобится фильтрация
_auth = require_token("logs:read")


@router.get("/errors")
def list_errors(tok=Depends(_auth), limit: int = Query(50, ge=1, le=500)):
    # TODO (Фаза 2): выборка ошибок владельца токена, без PII в ответе
    return {"items": [], "limit": limit}


@router.get("/sends")
def list_sends(tok=Depends(_auth), limit: int = Query(50, ge=1, le=500)):
    # TODO (Фаза 2): выборка отправок владельца токена, домен/хэш вместо полного email
    return {"items": [], "limit": limit}
