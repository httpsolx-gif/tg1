"""Общие ключи сервисов (OTHER → 🔑 Ключи): Cloudflare, NameSilo, Dynadot.

Хранятся в AppState (`key:<name>`) зашифрованными. Их берут флоу short-доменов
(создание зоны, авто-NS) и deploy/api_domain.py — вводить токены на каждый домен не нужно.
"""
from __future__ import annotations

from sqlmodel import Session

from common.db import get_engine
from common.models.appstate import AppState
from common.security import encrypt_password, decrypt_password

NAMES = {"cf": "Cloudflare", "namesilo": "NameSilo", "dynadot": "Dynadot"}

_engine = get_engine()


def get(name: str) -> str:
    with Session(_engine) as db:
        row = db.get(AppState, f"key:{name}")
    if not row or not row.value:
        return ""
    try:
        return decrypt_password(row.value) or ""
    except Exception:
        return ""


def set(name: str, value: str | None) -> None:  # noqa: A001
    """Сохранить ключ; пустое/None — удалить."""
    with Session(_engine) as db:
        row = db.get(AppState, f"key:{name}")
        if not value:
            if row:
                db.delete(row)
                db.commit()
            return
        if row is None:
            row = AppState(key=f"key:{name}")
        row.value = encrypt_password(value)
        db.add(row)
        db.commit()


def masked(name: str) -> str:
    v = get(name)
    return f"✅ …{v[-4:]}" if v else "❌ не задан"
