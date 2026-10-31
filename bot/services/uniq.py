"""Уникализация HTML-шаблона писем через htmlmix.pro (getRandomizeLetter).

Модель (п.107): у связки сервис×рубрика есть ОДИН активный уникализированный
шаблон. Он генерируется разом и хранится в самой строке конфига
(`service_rubric_config.uniq_html`); все письма до следующего обновления уходят
на нём. Обновление:
  • «вручную» — кнопкой «🔁 Сгенерировать сейчас»;
  • «по расписанию» — фон `schedule_loop` обновляет в заданные часы (напр. 08:00,
    13:00, 19:00 по времени сервера).
API htmlmix лимитирован 1 запросом в 60 с на аккаунт — при такой модели это не
мешает (генерим редко). Не сгенерировано / API недоступен / настройки или шаблон
изменились после генерации → письмо уходит с ИСХОДНЫМ шаблоном (не падаем).

Уникализируется тот же шаблон, что ушёл бы при отправке (с плейсхолдерами
*DATE*/*TITLE*/*URL*); подстановка значений — после, в `render_template`.
`uniq_sig` привязывает вариант к (исходный шаблон + опции): не совпало — вариант
считается устаревшим и не используется.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import datetime

import aiohttp
from sqlmodel import Session, select

from common.db import get_engine
from common.models.appstate import AppState
from common.models.service_config import ServiceRubricConfig
from bot.services import keys

log = logging.getLogger("bot")
_engine = get_engine()
_lock = asyncio.Lock()  # бот — один процесс-лидер

API_URL = "https://htmlmix.pro/api/?action=getRandomizeLetter"
RATE_SEC = 60                 # лимит htmlmix: 1 запрос / 60 с на аккаунт
_TS_KEY = "uniqfetch:ts"      # когда в последний раз дёргали API (общий кулдаун)
DEFAULT_TIMES = ["08:00", "13:00", "19:00"]
_TIME_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")

# (ключ, группа API, подпись, дефолт). group: morph = modeMorphing, set = settings.
OPTIONS: tuple[tuple[str, str, str, int], ...] = (
    ("insertGarbageCode", "set", "Мусорный код", 1),
    ("changeNameClass", "set", "Переименовать классы/id", 1),
    ("randomChangeTags", "set", "Менять теги местами", 1),
    ("addRandomComments", "set", "Случайные комментарии", 1),
    ("randomizeFonts", "set", "Случайные шрифты", 0),
    ("transformStructure", "set", "Перестановка структуры", 0),
    ("randomTableLayot", "morph", "Случайный табличный layout", 0),
    ("addPreviewText", "set", "Прехедер (preview text)", 0),
    ("synonymsAI", "morph", "⚠️ AI-синонимы", 0),
)
_LABEL = {k: lbl for k, _g, lbl, _d in OPTIONS}


def default_cfg() -> dict:
    return {"on": False, "mode": "manual", "times": list(DEFAULT_TIMES),
            "opts": {k: d for k, _g, _lbl, d in OPTIONS}}


def parse_cfg(raw: str | None) -> dict:
    base = default_cfg()
    if not raw:
        return base
    try:
        data = json.loads(raw)
    except Exception:
        return base
    base["on"] = bool(data.get("on"))
    base["mode"] = "schedule" if data.get("mode") == "schedule" else "manual"
    times = [t for t in (data.get("times") or []) if _TIME_RE.match(str(t))]
    base["times"] = times or list(DEFAULT_TIMES)
    opts = data.get("opts") or {}
    for k in base["opts"]:
        if k in opts:
            base["opts"][k] = 1 if opts[k] else 0
    return base


def dump_cfg(cfg: dict) -> str:
    return json.dumps({
        "on": bool(cfg.get("on")),
        "mode": "schedule" if cfg.get("mode") == "schedule" else "manual",
        "times": cfg.get("times") or list(DEFAULT_TIMES),
        "opts": cfg.get("opts") or {},
    }, ensure_ascii=False)


def enabled_count(cfg: dict) -> int:
    return sum(1 for v in (cfg.get("opts") or {}).values() if v)


def parse_times(text: str) -> list[str]:
    """«08:00 13:00, 19:00» → ['08:00','13:00','19:00'] (нормализовано, без дублей)."""
    out: list[str] = []
    for part in re.split(r"[\s,;]+", (text or "").strip()):
        if not part:
            continue
        m = _TIME_RE.match(part)
        if not m:
            raise ValueError(f"неверное время: {part} (нужен формат ЧЧ:ММ)")
        norm = f"{int(m.group(1)):02d}:{m.group(2)}"
        if norm not in out:
            out.append(norm)
    if not out:
        raise ValueError("не указано ни одного времени")
    return sorted(out)


# ───────────────────────────── API ─────────────────────────────
def _api_body(html: str, cfg: dict) -> dict:
    opts = cfg.get("opts") or {}
    morph = {k: int(opts.get(k, 0)) for k, g, _l, _d in OPTIONS if g == "morph"}
    settings = {k: int(opts.get(k, 0)) for k, g, _l, _d in OPTIONS if g == "set"}
    settings["fileCountGen"] = 1          # нужен один активный вариант
    settings["garbageToLink"] = 0         # не пачкать наши ссылки (*URL*)
    return {"source": html, "modeMorphing": morph, "settings": settings}


def _sig(html: str, cfg: dict) -> str:
    body = json.dumps(_api_body("", cfg), sort_keys=True)
    return hashlib.sha1(f"{body}\x00{html}".encode("utf-8", "replace")).hexdigest()[:40]


async def verify_token(token: str) -> tuple[bool, str]:
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    to = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=to) as s:
            async with s.post("https://htmlmix.pro/api/?action=getUserInfo", headers=headers) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        return False, f"{type(e).__name__}: {e}"
    if isinstance(data, dict) and data.get("status") == "success":
        u = data.get("user") or {}
        return True, f"подписка до {u.get('subscription_end_date', '?')}, квота {u.get('quota_api', '?')}"
    return False, str(data)[:200]


async def _fetch_one(html: str, cfg: dict) -> str | None:
    token = keys.get("htmlmix")
    if not token:
        log.warning("uniq: нет токена htmlmix (OTHER → 🔑 Ключи)")
        return None
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    to = aiohttp.ClientTimeout(total=120)
    try:
        async with aiohttp.ClientSession(timeout=to) as s:
            async with s.post(API_URL, headers=headers, json=_api_body(html, cfg)) as r:
                data = await r.json(content_type=None)
    except Exception as e:
        log.warning("uniq: запрос к htmlmix не удался: %s", e)
        return None
    if not isinstance(data, dict) or data.get("status") != "success":
        log.warning("uniq: htmlmix ответил ошибкой: %s", str(data)[:200])
        return None
    for h in (data.get("html") or []):
        if isinstance(h, str) and h.strip():
            return h
    log.warning("uniq: htmlmix вернул пустой список вариантов")
    return None


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


def cooldown_left() -> int:
    return max(0, int(RATE_SEC - (time.time() - _get_ts())))


# ───────────────────── конфиг-строки и шаблон ─────────────────────
def _cfg_row(db: Session, service: str, rid: int | None, mailbox: str):
    rcond = (ServiceRubricConfig.rubric_id == rid) if rid else (ServiceRubricConfig.rubric_id == None)  # noqa: E711
    return db.exec(select(ServiceRubricConfig).where(
        (ServiceRubricConfig.service == service) & rcond & (ServiceRubricConfig.mailbox == mailbox)
    )).first()


def _effective_row(db: Session, service: str, rubric_id: int | None, mailbox: str):
    """Строка, чья уникализация ВКЛЮЧЕНА, в порядке рубрика→ALL (как имя/тайтл)."""
    layers = ([(rubric_id, mailbox), (rubric_id, "")] if rubric_id else []) + [(None, mailbox), (None, "")]
    seen = set()
    for rid, mb in layers:
        if (rid, mb) in seen:
            continue
        seen.add((rid, mb))
        row = _cfg_row(db, service, rid, mb)
        if row and parse_cfg(row.uniq_opts)["on"]:
            return row
    return None


def _code_for(rid: int | None) -> str:
    if not rid:
        return ""
    from common.models.rubric import Rubric
    with Session(_engine) as db:
        r = db.get(Rubric, rid)
    return (r.name[:1] or "").lower() if r else ""


def _row_source(service: str, rid: int | None, mailbox: str) -> str:
    """Исходный шаблон строки — ровно тот, что ушёл бы при отправке этой связки."""
    from bot.services.send_core import resolve_send_template
    return resolve_send_template(service, _code_for(rid), rid, mailbox)


def _rubric_has_own_template(service: str, rid: int, code: str) -> bool:
    from bot.services.templates import resolve_template_html
    with Session(_engine) as db:
        row = _cfg_row(db, service, rid, "")
    return bool(row and row.template_html) or resolve_template_html(service, code) is not None


def active_variant(service: str, rubric_id: int | None, mailbox: str, send_html: str, cfg: dict) -> str | None:
    """Готовый вариант для отправки: сначала строка конкретной рубрики, потом ALL (fallback).
    Отдаём только если sig совпал с текущим шаблоном+опциями — иначе None (письмо на исходном)."""
    if not cfg.get("on") or not enabled_count(cfg):
        return None
    want = _sig(send_html, cfg)
    with Session(_engine) as db:
        for store_rid in (rubric_id, None):
            row = _cfg_row(db, service, int(store_rid) if store_rid else None, "")
            if row and row.uniq_html and row.uniq_sig == want:
                return row.uniq_html
    return None


async def _gen_core(service: str, store_rid: int | None, source: str, cfg: dict,
                    *, slot: str | None = None) -> tuple[str, str]:
    """Сгенерировать вариант из `source` и записать в строку (service, store_rid, '')."""
    if not source:
        return "nosrc", "нет исходного шаблона"
    async with _lock:
        if cooldown_left() > 0:
            return "wait", str(cooldown_left())
        _set_ts(time.time())
        variant = await _fetch_one(source, cfg)
    if not variant:
        return "apierr", "htmlmix не вернул вариант"
    with Session(_engine) as db:
        row = _cfg_row(db, service, int(store_rid) if store_rid else None, "")
        if not row:
            row = ServiceRubricConfig(service=service, rubric_id=(int(store_rid) if store_rid else None), mailbox="")
        row.uniq_html = variant
        row.uniq_sig = _sig(source, cfg)
        row.uniq_at = datetime.utcnow().isoformat(timespec="seconds")
        if slot:
            row.uniq_slot = slot
        db.add(row)
        db.commit()
    return "ok", "вариант обновлён"


async def generate(service: str, rid: int | None, mailbox: str) -> tuple[str, str]:
    """Ручная генерация. Рубрика — сразу её вариант. ALL — сбросить варианты рубрик (их
    дольёт фон) и обновить общий вариант. → ('ok'|'okall'|'off'|'wait'|'nosrc'|'apierr', инфо)."""
    with Session(_engine) as db:
        row = _cfg_row(db, service, int(rid) if rid else None, mailbox)
        cfg = parse_cfg(row.uniq_opts) if row else default_cfg()
    if not cfg["on"] or not enabled_count(cfg):
        return "off", "уникализация выключена"
    if rid:
        return await _gen_core(service, int(rid), _row_source(service, int(rid), ""), cfg)
    # ALL: пометить рубрики со своим шаблоном на долив (фон сделает по одной)
    from common.models.rubric import Rubric
    names: list[str] = []
    with Session(_engine) as db:
        own_cfg = {r.rubric_id for r in db.exec(select(ServiceRubricConfig).where(
            (ServiceRubricConfig.service == service) & (ServiceRubricConfig.rubric_id != None)  # noqa: E711
        )).all() if parse_cfg(r.uniq_opts)["on"]}
        for rb in db.exec(select(Rubric)).all():
            if rb.id in own_cfg:
                continue
            if _rubric_has_own_template(service, rb.id, (rb.name[:1] or "").lower()):
                r2 = _cfg_row(db, service, rb.id, "")
                if not r2:
                    r2 = ServiceRubricConfig(service=service, rubric_id=rb.id, mailbox="")
                r2.uniq_html = None
                r2.uniq_sig = None
                db.add(r2)
                names.append(rb.name)
        db.commit()
    st, info = await _gen_core(service, None, _row_source(service, None, ""), cfg)
    if names:
        return "okall", ", ".join(names)
    return st, info


def status_line(service: str, rid: int | None, mailbox: str, cfg: dict) -> str:
    """Статус готовности варианта(ов) для экрана настроек."""
    def _fresh(store_rid, src=None):
        with Session(_engine) as db:
            row = _cfg_row(db, service, int(store_rid) if store_rid else None, "")
        if not row or not row.uniq_html:
            return None, None
        try:
            ok = row.uniq_sig == _sig(src if src is not None else _row_source(service, store_rid, ""), cfg)
        except Exception:
            ok = True
        return row, ok
    if rid:
        row, ok = _fresh(int(rid))
        if not row:
            return "ещё не сгенерирован"
        when = (row.uniq_at or "").replace("T", " ")
        tail = "" if ok else " ⚠️ настройки/шаблон изменились — пересоздайте"
        return f"есть, от {when} UTC{tail}"
    # ALL: по рубрикам со своим шаблоном + «прочие» (общий вариант)
    from common.models.rubric import Rubric
    parts: list[str] = []
    with Session(_engine) as db:
        rubs = [(r.id, r.name) for r in db.exec(select(Rubric)).all()]
    for rb_id, name in rubs:
        code = (name[:1] or "").lower()
        if not _rubric_has_own_template(service, rb_id, code):
            continue
        _row, ok = _fresh(rb_id)
        parts.append(f"{name} {'✅' if ok else '⏳'}")
    _rowA, okA = _fresh(None)
    parts.append(f"прочие {'✅' if okA else '⏳'}")
    return "по рубрикам — " + ", ".join(parts)


# ───────────────────── фон: держим варианты свежими (у лидера) ─────────────────────
def _targets() -> list[tuple[str, int | None, dict]]:
    """Что поддерживать: (service, store_rid, cfg). Рубрики со своим конфигом + разворот ALL."""
    out: list[tuple[str, int | None, dict]] = []
    from common.models.rubric import Rubric
    with Session(_engine) as db:
        rows = db.exec(select(ServiceRubricConfig).where(
            ServiceRubricConfig.uniq_opts != None)).all()  # noqa: E711
        rubric_on: dict[tuple[str, int], dict] = {}
        all_on: dict[str, dict] = {}
        for r in rows:
            c = parse_cfg(r.uniq_opts)
            if not c["on"] or not enabled_count(c):
                continue
            if r.rubric_id:
                rubric_on[(r.service, r.rubric_id)] = c
            else:
                all_on[r.service] = c
        rubs = [(r.id, r.name) for r in db.exec(select(Rubric)).all()]
    for (service, rid), c in rubric_on.items():
        out.append((service, rid, c))
    for service, c in all_on.items():
        for rb_id, name in rubs:
            if (service, rb_id) in rubric_on:
                continue
            if _rubric_has_own_template(service, rb_id, (name[:1] or "").lower()):
                out.append((service, rb_id, c))
        out.append((service, None, c))  # общий вариант (рубрики без своего шаблона)
    return out


async def maintain_loop(bot=None) -> None:
    """Раз в минуту доливает недостающие/устаревшие варианты (вкл. разворот ALL по рубрикам)
    и обновляет по расписанию. Один вызов API за тик (лимит 1/60 с)."""
    while True:
        await asyncio.sleep(60)
        try:
            now = datetime.now()
            hhmm = now.strftime("%H:%M")
            slot = now.strftime("%Y-%m-%d ") + hhmm
            for service, store_rid, cfg in _targets():
                source = _row_source(service, store_rid, "")
                want = _sig(source, cfg)
                with Session(_engine) as db:
                    row = _cfg_row(db, service, int(store_rid) if store_rid else None, "")
                stale = (not row) or (not row.uniq_html) or (row.uniq_sig != want)
                sched_due = (cfg["mode"] == "schedule" and hhmm in cfg["times"]
                             and (not row or row.uniq_slot != slot))
                if not (stale or sched_due):
                    continue
                st, info = await _gen_core(service, store_rid, source, cfg,
                                           slot=slot if sched_due else None)
                if st == "wait":
                    break  # кулдаун — догоним на следующих тиках
                if st != "ok":
                    log.warning("uniq maintain %s/%s: %s %s", service, store_rid, st, info)
                break  # один за тик (кулдаун API)
        except Exception as e:
            log.warning("uniq maintain loop: %s", e)
