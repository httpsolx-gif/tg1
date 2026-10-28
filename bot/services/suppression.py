"""Suppression-лист: адреса, на которые не слать (п.47).

Чистый модуль (sqlmodel + common) — импортят и бот (UI), и send_core (проверка).
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlmodel import Session, select

from common.db import get_engine
from common.models.suppression import Suppression

_engine = get_engine()
_SPLIT = re.compile(r"[\s,;]+")


def _norm(email: str | None) -> str:
    return (email or "").strip().lower()


def is_suppressed(email: str | None) -> bool:
    e = _norm(email)
    if not e:
        return False
    with Session(_engine) as db:
        return db.get(Suppression, e) is not None


def add(email: str, reason: str | None = "manual") -> bool:
    e = _norm(email)
    if not e or "@" not in e:
        return False
    with Session(_engine) as db:
        row = db.get(Suppression, e)
        if row:
            row.reason = reason or row.reason
            db.add(row)
        else:
            db.add(Suppression(email=e, reason=reason, created_at=datetime.utcnow().isoformat()))
        db.commit()
    return True


def add_bulk(text: str, reason: str | None = "manual") -> int:
    n = 0
    for p in (x for x in _SPLIT.split(text or "") if x.strip()):
        if add(p, reason):
            n += 1
    return n


def remove(email: str) -> bool:
    e = _norm(email)
    with Session(_engine) as db:
        row = db.get(Suppression, e)
        if not row:
            return False
        db.delete(row)
        db.commit()
        return True


def list_all(limit: int = 100) -> list[Suppression]:
    with Session(_engine) as db:
        return db.exec(select(Suppression).order_by(Suppression.created_at.desc()).limit(limit)).all()


def count() -> int:
    with Session(_engine) as db:
        return len(db.exec(select(Suppression.email)).all())
