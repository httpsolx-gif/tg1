"""Уникализация HTML-шаблона писем через htmlmix.pro (getRandomizeLetter).

Зачем пул: API htmlmix отдаёт сразу пачку вариантов (`fileCountGen`), но лимитирован
1 запросом в 60 секунд НА ВЕСЬ аккаунт. Дёргать его на каждое письмо нельзя, поэтому
пачку кладём в таблицу `uniq_variant` и раздаём варианты по письмам из неё. Кончились
и 60 с с прошлого запроса не прошло → письмо уходит с исходным шаблоном (не падаем).

Уникализируется ИСХОДНЫЙ шаблон (с плейсхолдерами *DATE*/*TITLE* и т.п.): варианты
кэшируются по шаблону, а подстановка значений для конкретного получателя делается уже
после — в `render_template`. По умолчанию включены только структурные опции (мусорный
код, переименование классов, перестановка тегов, комментарии), которые не трогают
видимый текст и наши плейсхолдеры.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime

import aiohttp
from sqlmodel import Session, select

from common.db import get_engine
from common.models.appstate import AppState
from common.models.uniq_variant import UniqVariant
from bot.services import keys

log = logging.getLogger("bot")
_engine = get_engine()
_lock = asyncio.Lock()  # бот — один процесс-лидер; хватает процессного лока

API_URL = "https://htmlmix.pro/api/?action=getRandomizeLetter"
RATE_SEC = 60                 # лимит htmlmix: 1 запрос / 60 с на аккаунт
_TS_KEY = "uniqfetch:ts"      # когда в последний раз дёргали API (общий кулдаун)
DEFAULT_COUNT = 25            # сколько вариантов просить за один запрос

# Опции, которые показываем в настройках. (ключ, группа API, подпись, дефолт).
# Группа: morph = modeMorphing, set = settings. «⚠️» — могут трогать видимый текст.
OPTIONS: tuple[tuple[str, str, str, int], ...] = (
    ("insertGarbageCode", "set", "Мусорный код", 1),
    ("changeNameClass", "set", "Переименовать классы/id", 1),
    ("randomChangeTags", "set", "Менять теги местами", 1),
    ("addRandomComments", "set", "Случайные комментарии", 1),
    ("randomizeFonts", "set", "Случайные шрифты", 0),
    ("transformStructure", "set", "Перестановка структуры", 0),
    ("inlineStylesToClasses", "set", "Инлайн-стили → классы", 0),
    ("randomTableLayot", "morph", "Случайный табличный layout", 0),
    ("addPreviewText", "set", "Прехедер (preview text)", 0),
    ("wrapLettersInTags", "morph", "⚠️ Оборачивать слова в теги", 0),
    ("zeroFront", "morph", "⚠️ Zero-width вставки", 0),
    ("synonymsAI", "morph", "⚠️ AI-синонимы", 0),
)
_GROUP = {k: g for k, g, _lbl, _d in OPTIONS}
_LABEL = {k: lbl for k, _g, lbl, _d in OPTIONS}


def default_cfg() -> dict:
    """Настройки уникализации по умолчанию: выключено, дефолтные опции."""
    return {"on": False, "count": DEFAULT_COUNT,
            "opts": {k: d for k, _g, _lbl, d in OPTIONS}}


def parse_cfg(raw: str | None) -> dict:
    """JSON из БД → нормализованный dict (недостающие ключи добираются дефолтом)."""
    base = default_cfg()
    if not raw:
        return base
    try:
        data = json.loads(raw)
    except Exception:
        return base
    base["on"] = bool(data.get("on"))
    base["count"] = int(data.get("count") or DEFAULT_COUNT)
    opts = data.get("opts") or {}
    for k in base["opts"]:
        if k in opts:
            base["opts"][k] = 1 if opts[k] else 0
    return base


def dump_cfg(cfg: dict) -> str:
    return json.dumps({"on": bool(cfg.get("on")),
                       "count": int(cfg.get("count") or DEFAULT_COUNT),
                       "opts": cfg.get("opts") or {}}, ensure_ascii=False)


def enabled_count(cfg: dict) -> int:
    return sum(1 for v in (cfg.get("opts") or {}).values() if v)


def _api_body(html: str, cfg: dict) -> dict:
    opts = cfg.get("opts") or {}
    morph = {k: int(opts.get(k, 0)) for k, g, _l, _d in OPTIONS if g == "morph"}
    settings = {k: int(opts.get(k, 0)) for k, g, _l, _d in OPTIONS if g == "set"}
    settings["fileCountGen"] = int(cfg.get("count") or DEFAULT_COUNT)
    settings["garbageToLink"] = 0  # не дописывать мусорный query к нашим ссылкам (*URL*)
    return {"source": html, "modeMorphing": morph, "settings": settings}


def _sig(pool: str, html: str, cfg: dict) -> str:
    body = json.dumps(_api_body("", cfg), sort_keys=True)
    h = hashlib.sha1(f"{pool}\x00{body}\x00{html}".encode("utf-8", "replace")).hexdigest()
    return h[:40]


def pool_key(service: str, rubric_id: int | None, mailbox: str) -> str:
    return f"{service}|{rubric_id or 0}|{mailbox or ''}"


def _get_ts() -> float:
    with Session(_engine) as db:
        row = db.get(AppState, _TS_KEY)
    try:
        return float(row.value) if row and row.value else 0.0
    except Exception:
        return 0.0


def _set_ts(ts: float) -> None:
    with Session(_engine) as db:
        row = db.get(AppState, _TS_KEY) or AppState(key=_TS_KEY)
        row.value = str(ts)
        db.add(row)
        db.commit()


async def fetch_variants(html: str, cfg: dict, *, token: str | None = None) -> list[str]:
    """Запросить пачку вариантов у htmlmix. → список HTML (пустой — если не вышло)."""
    token = token or keys.get("htmlmix")
    if not token:
        log.warning("uniq: нет токена htmlmix (OTHER → 🔑 Ключи)")
        return []
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    to = aiohttp.ClientTimeout(total=120)
    try:
        async with aiohttp.ClientSession(timeout=to) as s:
            async with s.post(API_URL, headers=headers, json=_api_body(html, cfg)) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        log.warning("uniq: запрос к htmlmix не удался: %s", e)
        return []
    if not isinstance(data, dict) or data.get("status") != "success":
        log.warning("uniq: htmlmix ответил ошибкой: %s", str(data)[:200])
        return []
    out = [h for h in (data.get("html") or []) if isinstance(h, str) and h.strip()]
    if not out:
        log.warning("uniq: htmlmix вернул пустой список вариантов")
    return out


async def verify_token(token: str) -> tuple[bool, str]:
    """Проверить токен htmlmix (getUserInfo). → (ok, инфо/ошибка)."""
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    to = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=to) as s:
            async with s.post("https://htmlmix.pro/api/?action=getUserInfo",
                              headers=headers) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if isinstance(data, dict) and data.get("status") == "success":
        u = data.get("user") or {}
        return True, f"подписка до {u.get('subscription_end_date', '?')}, квота {u.get('quota_api', '?')}"
    return False, str(data)[:200]


def _take_unused(db: Session, pool: str, sig: str) -> str | None:
    row = db.exec(select(UniqVariant).where(
        (UniqVariant.pool == pool) & (UniqVariant.sig == sig) & (UniqVariant.used == False)  # noqa: E712
    ).limit(1)).first()
    if not row:
        return None
    row.used = True
    db.add(row)
    db.commit()
    return row.html


async def pick_variant(pool: str, html: str, cfg: dict) -> str:
    """Отдать уникализированный вариант шаблона (или исходный html при любой заминке).

    Берёт неиспользованный из пула; пусто и кулдаун прошёл → доливает пачку.
    Исключений наружу не бросает.
    """
    try:
        if not cfg.get("on") or not enabled_count(cfg):
            return html
        sig = _sig(pool, html, cfg)
        async with _lock:
            with Session(_engine) as db:
                got = _take_unused(db, pool, sig)
            if got is not None:
                return got
            # пул пуст: можно ли дёрнуть API (общий кулдаун 60 с)?
            now = time.time()
            if now - _get_ts() < RATE_SEC:
                return html  # ещё рано — письмо уходит с исходным шаблоном
            _set_ts(now)
            variants = await fetch_variants(html, cfg)
            if not variants:
                return html
            created = datetime.utcnow().isoformat()
            with Session(_engine) as db:
                # долив = свежая пачка: старые варианты этой области больше не нужны
                for old in db.exec(select(UniqVariant).where(UniqVariant.pool == pool)).all():
                    db.delete(old)
                for h in variants:
                    db.add(UniqVariant(pool=pool, sig=sig, html=h, used=False, created=created))
                db.commit()
                got = _take_unused(db, pool, sig)
            return got if got is not None else html
    except Exception as e:
        log.warning("uniq: pick_variant упал, шлём исходный шаблон: %s", e)
        return html


def pool_stats(pool: str) -> tuple[int, int]:
    """(всего в пуле, свободно) — для экрана настроек."""
    with Session(_engine) as db:
        rows = db.exec(select(UniqVariant).where(UniqVariant.pool == pool)).all()
    return len(rows), sum(1 for r in rows if not r.used)


def clear_pool(pool: str) -> None:
    with Session(_engine) as db:
        for r in db.exec(select(UniqVariant).where(UniqVariant.pool == pool)).all():
            db.delete(r)
        db.commit()
