"""Идемпотентность приёма отправки (п.47).

Ключ скоупится на токен: PK = "{token_id}:{key}". Чистый модуль.
"""
from __future__ import annotations

import json
from datetime import datetime

from sqlmodel import Session

from common.db import get_engine
from common.models.idempotency import IdemKey

_engine = get_engine()


def _pk(token_id: int, key: str) -> str:
    return f"{token_id}:{(key or '').strip()}"


def get(token_id: int, key: str) -> dict | None:
    """Прежний ответ по ключу или None."""
    if not (key or "").strip():
        return None
    with Session(_engine) as db:
        row = db.get(IdemKey, _pk(token_id, key))
    if not row:
        return None
    try:
        return json.loads(row.response)
    except Exception:
        return None


def save(token_id: int, key: str, response: dict) -> None:
    if not (key or "").strip():
        return
    try:
        with Session(_engine) as db:
            pk = _pk(token_id, key)
            if db.get(IdemKey, pk):
                return  # первый ответ не перезаписываем
            db.add(IdemKey(id=pk, response=json.dumps(response),
                           created_at=datetime.utcnow().isoformat()))
            db.commit()
    except Exception:
        pass
