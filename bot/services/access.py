"""Классификация отправителей (ban/white/black) + CRUD списков доступа.

Идентификация по числовому id ИЛИ @username (сверяем оба при отправке).
Правило: админ → white; в ban → 'ban' (блок); в white → 'white' (обычная);
иначе (явный black или нигде не числится) → 'black'.

Профиль black (шаблон/тайтл/имя/SMTP/сокра) живёт в рубрике «black» и
настраивается в 🧩 Сервисы по этой рубрике (bot/handlers/admin/rubrics).
"""
from __future__ import annotations

import re

from sqlmodel import Session, select

from common.db import get_engine
from common.models.access import AccessEntry

_engine = get_engine()

LISTS = ("ban", "white", "black")
LIST_RU = {"ban": "🚫 BAN", "white": "✅ White", "black": "⚫️ Black"}

_SPLIT = re.compile(r"[\s,;]+")


def _norm(raw: str):
    """'@User' → ('user', False); '12345' → ('12345', True); пусто → None."""
    s = (raw or "").strip().lstrip("@").lower()
    if not s:
        return None
    return (s, True) if s.isdigit() else (s, False)


def add_entries(lst: str, text: str) -> tuple[int, int]:
    """Добавить пачку (id/@username через пробел/запятую/перенос). → (добавлено, пропущено)."""
    added = skipped = 0
    with Session(_engine) as db:
        for p in (x for x in _SPLIT.split(text or "") if x.strip()):
            n = _norm(p)
            if not n:
                continue
            val, is_id = n
            if db.exec(select(AccessEntry).where(
                    (AccessEntry.lst == lst) & (AccessEntry.value == val))).first():
                skipped += 1
                continue
            db.add(AccessEntry(lst=lst, value=val, is_id=is_id))
            added += 1
        db.commit()
    return added, skipped


def remove_entry(entry_id: int) -> bool:
    with Session(_engine) as db:
        e = db.get(AccessEntry, entry_id)
        if not e:
            return False
        db.delete(e)
        db.commit()
        return True


def list_entries(lst: str) -> list[AccessEntry]:
    with Session(_engine) as db:
        return db.exec(select(AccessEntry).where(AccessEntry.lst == lst).order_by(AccessEntry.id)).all()


def classify(uid: int, username: str | None, admin_ids: set) -> str:
    """'ban' | 'white' | 'black'. Админы — всегда white (обычная отправка)."""
    if uid in (admin_ids or set()):
        return "white"
    keys = {str(uid)}
    if username:
        keys.add(username.strip().lstrip("@").lower())
    with Session(_engine) as db:
        lists = {r.lst for r in db.exec(select(AccessEntry).where(AccessEntry.value.in_(keys))).all()}
    if "ban" in lists:
        return "ban"
    if "white" in lists:
        return "white"
    return "black"  # не в white и не в ban → black (даже если явно в black или нигде)
