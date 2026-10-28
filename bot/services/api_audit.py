"""Запись/чтение аудита вызовов API (п.47). Чистый модуль (sqlmodel + common)."""
from __future__ import annotations

from datetime import datetime

from sqlmodel import Session, select

from common.db import get_engine
from common.models.api_audit import ApiAudit

_engine = get_engine()


def record(token_id: int | None, token_prefix: str | None, ip: str | None,
           method: str | None, path: str | None, status: int | None) -> None:
    """Записать один вызов. Исключения глушим — аудит не должен ломать ответ."""
    try:
        with Session(_engine) as db:
            db.add(ApiAudit(
                created_at=datetime.utcnow().isoformat(),
                token_id=token_id, token_prefix=token_prefix,
                ip=ip, method=method, path=path, status=status,
            ))
            db.commit()
    except Exception:
        pass


def recent(token_id: int | None = None, limit: int = 20) -> list[ApiAudit]:
    with Session(_engine) as db:
        q = select(ApiAudit)
        if token_id is not None:
            q = q.where(ApiAudit.token_id == token_id)
        return db.exec(q.order_by(ApiAudit.id.desc()).limit(limit)).all()
