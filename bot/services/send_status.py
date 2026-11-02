"""Статус отправки для клиента API (п.54).

Клиенту отдаём только его данные (строки SendLog с его api_token_id): полный адрес
получателя, машинный `status` + человекочитаемый `label` на русском:

    ok   → sent      «Отправлено»
    fail → error     «Ошибка отправки»
    (нет строки, но задача в очереди) → pending «В обработке» (решает роут)
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select, desc

from common.db import get_engine
from common.models.send_log import SendLog

_engine = get_engine()

# машинный статус SendLog.status → (code, label)
_LABELS = {
    "ok": ("sent", "Отправлено"),
    "fail": ("error", "Ошибка отправки"),
}


def _row_view(row: SendLog) -> dict:
    """Представление строки лога для клиента (без внутренних данных сервиса)."""
    code, label = _LABELS.get(row.status or "", ("unknown", "Неизвестно"))
    out = {
        "id": row.job_id,
        "status": code,
        "label": label,
        "email": row.email,
        "country": row.country,
        "service": row.service,
        "created_at": row.created_at,
    }
    if code == "error":
        out["error"] = row.error or "send failed"
    return out


def status_of_job(job_id: str, api_token_id: int) -> Optional[dict]:
    """Статус конкретной отправки этого токена по job_id. None — строки ещё/уже нет
    (решение «в обработке» vs «неизвестно» принимает роут по воркеру)."""
    if not job_id:
        return None
    with Session(_engine) as db:
        row = db.exec(
            select(SendLog).where(
                (SendLog.job_id == job_id) & (SendLog.api_token_id == api_token_id)
            )
        ).first()
    return _row_view(row) if row else None


def recent_sends(api_token_id: int, limit: int = 50, only_errors: bool = False) -> list[dict]:
    """Последние отправки этого токена (свежие сверху). only_errors → лишь неуспешные."""
    with Session(_engine) as db:
        q = select(SendLog).where(SendLog.api_token_id == api_token_id)
        if only_errors:
            q = q.where(SendLog.status == "fail")
        rows = db.exec(q.order_by(desc(SendLog.id)).limit(limit)).all()
    return [_row_view(r) for r in rows]
