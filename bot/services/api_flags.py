"""Глобальные флаги API (п.48, п.49). Хранятся в app_state (key-value),
переключаются в боте (🛡 API).

- api_paused — режим техработ: POST /api/v1/send отдаёт 503 (kill-switch приёма).
- api_dedup — антидубль: не слать повтор на тот же email (окно DEDUP_HOURS).
  По умолчанию ВЫКЛ — дубли разрешены (решаем мы, не клиент).
"""
from __future__ import annotations

from sqlmodel import Session

from common.db import get_engine
from common.models.appstate import AppState

_engine = get_engine()

DEDUP_HOURS = 24  # окно антидубля: повтор на тот же email в пределах этого срока


def _get(key: str) -> bool:
    with Session(_engine) as db:
        row = db.get(AppState, key)
        return bool(row and row.value == "1")


def _set(key: str, on: bool) -> None:
    with Session(_engine) as db:
        row = db.get(AppState, key)
        if row:
            row.value = "1" if on else "0"
            db.add(row)
        else:
            db.add(AppState(key=key, value="1" if on else "0"))
        db.commit()


def is_paused() -> bool:
    return _get("api_paused")


def set_paused(on: bool) -> None:
    _set("api_paused", on)


def is_dedup() -> bool:
    return _get("api_dedup")


def set_dedup(on: bool) -> None:
    _set("api_dedup", on)
