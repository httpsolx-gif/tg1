"""Каталог стран и сервисов в БД (п.61) — единый источник для отправки, 🧩 Сервисы,
SMTP, SHORT, API и спам-проверки.

Таблицы: `country` (code, flag, sort) и `service` (name, slug, country, active).
Управляется из админки: 🧩 SRVS → Сервисы (➕ страна / ➕ сервис / 🗑).
Пустые таблицы при первом обращении засеиваются из common/constants.py
(COUNTRIES + COUNTRY_SERVICES) — дальше константы не используются.

Чтения не кэшируются: таблицы маленькие, а у бота и API — разные процессы,
кэш разъехался бы после правки из админки.
"""
from __future__ import annotations

import re
from typing import Optional

from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, select

from common.db import get_engine
from common.models.country import Country
from common.models.service import Service

_engine = get_engine()
_seeded = False

# Сервис — домен-подобное имя: латиница/цифры/точка/дефис. Длина ограничена, чтобы
# callback_data вида «sco:o:{CC}:{service}:{rubric_id}» влезала в лимит Telegram 64 байта.
SERVICE_MAX_LEN = 40
_SERVICE_RE = re.compile(r"^[a-z0-9][a-z0-9.\-]*[a-z0-9]$")
_CODE_RE = re.compile(r"^[A-Z]{2}$")


def flag_for(code: str) -> str:
    """ISO-код → эмодзи-флаг (региональные индикаторы)."""
    code = (code or "").upper()
    if not _CODE_RE.match(code):
        return ""
    return "".join(chr(0x1F1E6 + ord(ch) - ord("A")) for ch in code)


def ensure_seeded() -> None:
    """Засеять каталог из констант, если таблицы пустые (один раз на процесс)."""
    global _seeded
    if _seeded:
        return
    SQLModel.metadata.create_all(_engine, tables=[Country.__table__, Service.__table__])
    with Session(_engine) as db:
        if db.exec(select(Country)).first() is None:
            from common.constants import COUNTRIES, COUNTRY_FLAGS, COUNTRY_SERVICES
            for i, code in enumerate(COUNTRIES):
                db.add(Country(code=code, flag=COUNTRY_FLAGS.get(code) or flag_for(code), sort=i))
            if db.exec(select(Service)).first() is None:
                for code in COUNTRIES:
                    for name in COUNTRY_SERVICES.get(code) or []:
                        db.add(Service(name=name, slug=name, country=code, active=True))
            try:
                db.commit()
            except IntegrityError:  # бот и API засеивали одновременно — второй просто опоздал
                db.rollback()
    _seeded = True


# ----------------------------- чтение -----------------------------
def countries() -> list[Country]:
    """Все страны каталога по порядку (включая страны без сервисов)."""
    ensure_seeded()
    with Session(_engine) as db:
        rows = db.exec(select(Country).order_by(Country.sort, Country.code)).all()
        for r in rows:
            db.expunge(r)
    return list(rows)


def country_flag(code: str) -> str:
    ensure_seeded()
    with Session(_engine) as db:
        c = db.get(Country, (code or "").upper())
        return (c.flag if c else "") or flag_for(code)


def services(country: str) -> list[str]:
    """Имена активных сервисов страны (в порядке добавления)."""
    ensure_seeded()
    with Session(_engine) as db:
        return list(db.exec(
            select(Service.name).where((Service.country == (country or "").upper()) & (Service.active == True))  # noqa: E712
            .order_by(Service.id)
        ).all())


def country_services(only_with_services: bool = False) -> dict[str, list[str]]:
    """{код: [сервисы]} в порядке стран. only_with_services — без пустых стран."""
    ensure_seeded()
    with Session(_engine) as db:
        rows = db.exec(select(Service).where(Service.active == True).order_by(Service.id)).all()  # noqa: E712
    by_country: dict[str, list[str]] = {}
    for s in rows:
        by_country.setdefault(s.country, []).append(s.name)
    out: dict[str, list[str]] = {}
    for c in countries():
        svcs = by_country.get(c.code, [])
        if svcs or not only_with_services:
            out[c.code] = svcs
    return out


def service_row(country: str, name: str) -> Optional[Service]:
    ensure_seeded()
    with Session(_engine) as db:
        s = db.exec(select(Service).where((Service.country == country) & (Service.name == name))).first()
        if s:
            db.expunge(s)
        return s


# ----------------------------- правка (админка) -----------------------------
def norm_code(raw: str) -> Optional[str]:
    code = (raw or "").strip().upper()
    return code if _CODE_RE.match(code) else None


def norm_service(raw: str) -> Optional[str]:
    name = (raw or "").strip().lower()
    if len(name) > SERVICE_MAX_LEN or not _SERVICE_RE.match(name):
        return None
    return name


def add_country(code: str) -> tuple[bool, str]:
    """(ok, сообщение). code — уже нормализованный ISO-код."""
    ensure_seeded()
    with Session(_engine) as db:
        if db.get(Country, code):
            return False, f"{code} уже есть"
        last = db.exec(select(Country).order_by(Country.sort.desc())).first()
        db.add(Country(code=code, flag=flag_for(code), sort=(last.sort + 1) if last else 0))
        db.commit()
    return True, f"{flag_for(code)} {code} добавлена"


def delete_country(code: str) -> int:
    """Удалить страну и все её сервисы. → сколько сервисов удалено."""
    ensure_seeded()
    with Session(_engine) as db:
        svcs = db.exec(select(Service).where(Service.country == code)).all()
        for s in svcs:
            db.delete(s)
        c = db.get(Country, code)
        if c:
            db.delete(c)
        db.commit()
        return len(svcs)


def add_services(country: str, names: list[str]) -> tuple[list[str], list[str], list[str]]:
    """Добавить сервисы стране. → (добавлены, уже были, невалидные)."""
    ensure_seeded()
    added, dup, bad = [], [], []
    with Session(_engine) as db:
        existing = set(db.exec(select(Service.name).where(Service.country == country)).all())
        for raw in names:
            name = norm_service(raw)
            if not name:
                bad.append(raw.strip())
                continue
            if name in existing:
                dup.append(name)
                continue
            db.add(Service(name=name, slug=name, country=country, active=True))
            existing.add(name)
            added.append(name)
        db.commit()
    return added, dup, bad


def delete_service(country: str, name: str) -> bool:
    ensure_seeded()
    with Session(_engine) as db:
        s = db.exec(select(Service).where((Service.country == country) & (Service.name == name))).first()
        if not s:
            return False
        db.delete(s)
        db.commit()
        return True
