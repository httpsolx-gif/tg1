"""Доступ к конфигу/целям авторассылки (п.56) — общий для админки бота и userbot.

Чистые функции над БД, без aiogram/telethon, чтобы импортировать с обеих сторон.
Конфиг — одна строка BcState (id=1), цели — BcTarget.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import List, Tuple

from sqlmodel import Session, select

from common.db import get_engine
from common.models.broadcast import BcState, BcTarget

_engine = get_engine()


# ------------------------------- конфиг (BcState) -------------------------------
def ensure_state() -> None:
    """Создать строку конфига id=1, если её ещё нет."""
    with Session(_engine) as db:
        if db.get(BcState, 1) is None:
            db.add(BcState(id=1))
            db.commit()


def get_state() -> BcState:
    """Отсоединённая копия конфига (гарантированно существует)."""
    with Session(_engine) as db:
        st = db.get(BcState, 1)
        if st is None:
            st = BcState(id=1)
            db.add(st)
            db.commit()
            db.refresh(st)
        db.expunge(st)
    return st


def update_state(**fields) -> None:
    with Session(_engine) as db:
        st = db.get(BcState, 1) or BcState(id=1)
        for k, v in fields.items():
            setattr(st, k, v)
        db.add(st)
        db.commit()


# ------------------------------- цели (BcTarget) -------------------------------
def list_targets(active_only: bool = False) -> List[BcTarget]:
    with Session(_engine) as db:
        q = select(BcTarget).order_by(BcTarget.id)
        if active_only:
            q = q.where(BcTarget.active == True)  # noqa: E712
        rows = db.exec(q).all()
        for r in rows:
            db.expunge(r)
    return rows


def add_targets(refs: List[str]) -> Tuple[int, int]:
    """Добавить цели (строки-ссылки). Возвращает (добавлено, дубликатов)."""
    added = dup = 0
    now = datetime.utcnow().isoformat()
    with Session(_engine) as db:
        existing = {t.chat_ref for t in db.exec(select(BcTarget)).all()}
        for raw in refs:
            ref = raw.strip()
            if not ref:
                continue
            if ref in existing:
                dup += 1
                continue
            db.add(BcTarget(chat_ref=ref, active=True, created_at=now))
            existing.add(ref)
            added += 1
        db.commit()
    return added, dup


def toggle_target(tid: int) -> bool | None:
    with Session(_engine) as db:
        t = db.get(BcTarget, tid)
        if not t:
            return None
        t.active = not t.active
        db.add(t)
        db.commit()
        return t.active


def delete_target(tid: int) -> bool:
    with Session(_engine) as db:
        t = db.get(BcTarget, tid)
        if not t:
            return False
        db.delete(t)
        db.commit()
        return True


def mark_target(tid: int, *, ok: bool, info: str | None = None) -> None:
    now = datetime.utcnow().isoformat()
    with Session(_engine) as db:
        t = db.get(BcTarget, tid)
        if not t:
            return
        if ok:
            t.last_ok = now
            t.last_error = None
        else:
            t.last_error = (info or "error")[:200]
        db.add(t)
        db.commit()


# ------------------------------- счётчики суток -------------------------------
def reset_day_if_needed() -> None:
    today = date.today().isoformat()
    with Session(_engine) as db:
        st = db.get(BcState, 1)
        if st and st.day != today:
            st.day = today
            st.sent_today = 0
            db.add(st)
            db.commit()


def bump_sent(n: int = 1) -> None:
    with Session(_engine) as db:
        st = db.get(BcState, 1)
        if st:
            st.sent_today = (st.sent_today or 0) + n
            db.add(st)
            db.commit()
