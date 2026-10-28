"""Suppression-лист: кому НЕ слать (п.47, п.50).

Три уровня проверки в is_suppressed(email):
1. Роль-адреса (local-part на любом домене): abuse@/postmaster@/spamtrap@… —
   из `constants.SUPPRESS_ROLES` (встроенные, всегда).
2. Домены (и их поддомены): площадки/мониторинг/ловушки — из
   `constants.SUPPRESS_DOMAINS` (встроенные) + записи `@domain` в таблице.
3. Точный адрес — запись email в таблице.

Таблица пополняется в боте (🛡 API → Suppression): email или `@domain`/голый домен.
Чистый модуль (sqlmodel + common) — зовут и бот (UI), и send_core (проверка).
"""
from __future__ import annotations

import re
from datetime import datetime

from sqlmodel import Session, select

from common.db import get_engine
from common.models.suppression import Suppression
from common.constants import SUPPRESS_ROLES, SUPPRESS_DOMAINS

_engine = get_engine()
_SPLIT = re.compile(r"[\s,;]+")


def _norm(s: str | None) -> str:
    return (s or "").strip().lower()


def _split_email(email: str) -> tuple[str, str] | None:
    """('abuse','mail.kleinanzeigen.de') или None, если не e-mail."""
    e = _norm(email)
    if "@" not in e:
        return None
    local, _, dom = e.partition("@")
    if not local or "." not in dom:
        return None
    return local, dom


def _domain_candidates(dom: str):
    """Домен и его родительские домены (кроме TLD): mail.kleinanzeigen.de →
    mail.kleinanzeigen.de, kleinanzeigen.de."""
    parts = dom.split(".")
    for i in range(len(parts) - 1):
        yield ".".join(parts[i:])


def is_suppressed(email: str | None) -> bool:
    pair = _split_email(email or "")
    if not pair:
        return False
    local, dom = pair
    # 1) роль-адрес на любом домене
    if local in SUPPRESS_ROLES:
        return True
    cands = list(_domain_candidates(dom))
    # 2) встроенные домены (вкл. поддомены)
    if any(c in SUPPRESS_DOMAINS for c in cands):
        return True
    # 3) таблица: точный email или @domain
    with Session(_engine) as db:
        if db.get(Suppression, f"{local}@{dom}"):
            return True
        for c in cands:
            if db.get(Suppression, "@" + c):
                return True
    return False


def _key_for(value: str) -> str | None:
    """Нормализовать ввод в ключ: email → 'a@b.c'; '@domain'/голый домен → '@domain'."""
    v = _norm(value)
    if not v:
        return None
    if v.startswith("@"):
        dom = v[1:].strip()
        return "@" + dom if "." in dom else None
    if "@" in v:
        return v if _split_email(v) else None
    if "." in v:                 # голый домен → блок домена
        return "@" + v
    return None


def add(value: str, reason: str | None = "manual") -> bool:
    key = _key_for(value)
    if not key:
        return False
    with Session(_engine) as db:
        row = db.get(Suppression, key)
        if row:
            row.reason = reason or row.reason
            db.add(row)
        else:
            db.add(Suppression(email=key, reason=reason, created_at=datetime.utcnow().isoformat()))
        db.commit()
    return True


def add_bulk(text: str, reason: str | None = "manual") -> int:
    n = 0
    for p in (x for x in _SPLIT.split(text or "") if x.strip()):
        if add(p, reason):
            n += 1
    return n


def remove(value: str) -> bool:
    key = _key_for(value) or _norm(value)
    with Session(_engine) as db:
        row = db.get(Suppression, key)
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
