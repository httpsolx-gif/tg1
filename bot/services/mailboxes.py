"""Управляемые типы почт (gmail/main + кастомные).

Единый источник правды для: кодов в именах файлов шаблонов, почт рубрик,
категорий SMTP. Дефолты сидятся один раз при первом обращении.
"""
from __future__ import annotations

from typing import List, Tuple

from sqlmodel import Session, select

from common.db import get_engine
from common.models.mailbox import Mailbox
from common.constants import MAILBOXES as _DEFAULTS  # [(code, name), ...]

_engine = get_engine()


def ensure_seeded() -> None:
    with Session(_engine) as db:
        if db.exec(select(Mailbox.id).limit(1)).first():
            return
        for code, name in _DEFAULTS:
            db.add(Mailbox(code=code, name=name, is_builtin=True))
        db.commit()


def all_mailboxes() -> List[Mailbox]:
    ensure_seeded()
    with Session(_engine) as db:
        return db.exec(select(Mailbox).order_by(Mailbox.id)).all()


def pairs() -> List[Tuple[str, str]]:
    """[(code, name), ...] в порядке добавления."""
    return [(m.code, m.name) for m in all_mailboxes()]


def code_to_name() -> dict:
    return {m.code: m.name for m in all_mailboxes()}


def names() -> List[str]:
    return [m.name for m in all_mailboxes()]


def add_mailbox(code: str, name: str) -> Mailbox:
    code = (code or "").strip().lower()
    name = (name or "").strip().lower()
    if not code or not name:
        raise ValueError("нужны и код, и имя")
    if not code.isalnum():
        raise ValueError("код — только буквы/цифры")
    ensure_seeded()
    with Session(_engine) as db:
        clash = db.exec(
            select(Mailbox).where((Mailbox.code == code) | (Mailbox.name == name))
        ).first()
        if clash:
            raise ValueError(f"код/имя заняты ({clash.code}={clash.name})")
        m = Mailbox(code=code, name=name, is_builtin=False)
        db.add(m)
        db.commit()
        db.refresh(m)
        return m


import re as _re
_DOM_SPLIT = _re.compile(r"[\s,;]+")


def domains_of(m: Mailbox) -> set:
    return {d.strip().lower() for d in _DOM_SPLIT.split(m.domains or "") if d.strip()}


def mailbox_for_email(email: str, default: str = "main") -> str:
    """Вернуть имя типа почты по домену получателя (gmx.de→main, gmail.com→gmail).
    Если домен ни к кому не привязан — default."""
    dom = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not dom:
        return default
    from common.domains import domain_in
    boxes = all_mailboxes()
    for m in boxes:
        if domain_in(dom, m.domains or ""):
            return m.name
    # корп/кастомный домен — определить провайдера по MX и привязать к типу почты
    try:
        from bot.services.mxlookup import mailbox_for_domain
        mb = mailbox_for_domain(dom)
        if mb and mb in {m.name for m in boxes}:
            return mb
    except Exception:
        pass
    return default


def set_domains(mailbox_id: int, domains_str: str) -> bool:
    with Session(_engine) as db:
        m = db.get(Mailbox, mailbox_id)
        if not m:
            return False
        m.domains = (domains_str or "").strip() or None
        db.add(m)
        db.commit()
        return True


def remove_mailbox(mailbox_id: int) -> bool:
    with Session(_engine) as db:
        m = db.get(Mailbox, mailbox_id)
        if not m:
            return False
        db.delete(m)
        db.commit()
        return True
