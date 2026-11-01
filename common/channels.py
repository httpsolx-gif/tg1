"""Каналы уведомлений (п.118): id задаются в боте (OTHER → 📣 Каналы), фолбэк — .env.

Хранятся в AppState `chan:<kind>`. Читаются часто (каждый лог-рекорд), поэтому кэш
на CACHE_SEC: смена в боте доходит до всех процессов (бот, API) за полминуты.
"""
from __future__ import annotations

import time

from common.config import settings
from common.tgids import norm_chat_id

# kind → (подпись, переменная .env для фолбэка, что туда идёт)
KINDS: dict[str, tuple[str, str, str]] = {
    "switch": ("🔁 Автосмены", "", "смена аккаунта в базе области, «все исчерпаны»"),
    "err": ("🚨 Ошибки и атаки", "ERR_CHANNEL_ID",
            "WARNING/ERROR, сбои отправки, флуд сокращалки, брутфорс SSH нод, rate limit CF"),
    "send": ("📤 Отправки", "SEND_CHANNEL_ID", "каждая отправка (✅/❌)"),
    "spam": ("📬 Спам-проверка", "SPAMCHECK_CHANNEL_ID", "карточки «Входящие / СПАМ»"),
    "backup": ("🗄 Бэкапы", "BACKUP_CHANNEL_ID", "дампы БД; после отправки файл удаляется с сервера"),
}

CACHE_SEC = 30
_cache: dict[str, tuple[float, str]] = {}


def _key(kind: str) -> str:
    return f"chan:{kind}"


def _from_db(kind: str) -> str | None:
    try:
        from sqlmodel import Session
        from common.db import get_engine
        from common.models.appstate import AppState
        with Session(get_engine()) as db:
            row = db.get(AppState, _key(kind))
            return row.value if row else None
    except Exception:
        return None


def get(kind: str) -> str:
    """chat_id канала ('' — не задан). Значение из бота важнее .env; «-» в боте = выключен."""
    now = time.monotonic()
    hit = _cache.get(kind)
    if hit and now - hit[0] < CACHE_SEC:
        return hit[1]
    val = _from_db(kind)
    if val is None:
        env = KINDS.get(kind, ("", "", ""))[1]
        val = getattr(settings, env, "") if env else ""
    val = "" if (val or "").strip() in ("", "-") else norm_chat_id(val)
    _cache[kind] = (now, val)
    return val


def source(kind: str) -> str:
    """Откуда значение: 'bot' | 'env' | ''."""
    v = _from_db(kind)
    if v is not None:
        return "bot" if v.strip() not in ("", "-") else ""
    env = KINDS.get(kind, ("", "", ""))[1]
    return "env" if env and (getattr(settings, env, "") or "").strip() else ""


def set(kind: str, value: str | None) -> None:
    """Задать канал из бота. None — убрать настройку (снова .env), '-' — выключить."""
    from sqlmodel import Session
    from common.db import get_engine
    from common.models.appstate import AppState
    with Session(get_engine()) as db:
        row = db.get(AppState, _key(kind))
        if value is None:
            if row:
                db.delete(row)
        else:
            row = row or AppState(key=_key(kind))
            row.value = value.strip()
            db.add(row)
        db.commit()
    _cache.pop(kind, None)
