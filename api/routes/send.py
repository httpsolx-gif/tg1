"""POST /api/v1/send — запуск отправки по токену (scope `send`) + GET /api/v1/catalog.

Клиент задаёт ТОЛЬКО: страну, сервис (из каталога), email, ссылку и подстановки
`price`→`*PRICE*`, `predmetname`→`*PNAME*`; `id`/`username` идут только в лог/трек.
Шаблон/тему/From/SMTP/сокру ставит система (общее ядро send_core) — клиент на них
не влияет (анти-релей). Содержимое не отправляется синхронно: валидация → квота/рейт
→ очередь → воркер (api/worker.py).
"""
from __future__ import annotations

import re
import secrets
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from pydantic import BaseModel

from api.auth import require_token
from api import worker
from bot.services import api_tokens as T

router = APIRouter(prefix="/api/v1", tags=["send"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LINK_RE = re.compile(r"^https?://([^/\s]+)", re.I)


class SendIn(BaseModel):
    country: str
    service: str
    email: str
    link: str
    id: Optional[str] = None             # Telegram userid (обязателен) — в лог/учёт
    username: Optional[str] = None       # Telegram @username (обязателен) — в лог/учёт
    price: Optional[str] = None          # цена товара без валюты → *PRICE*
    predmetname: Optional[str] = None    # название товара → *PNAME*


def _catalog() -> dict:
    """Доступные страны и сервисы (из констант; только страны с сервисами)."""
    from common.constants import COUNTRY_SERVICES, COUNTRY_FLAGS
    return {c: {"flag": COUNTRY_FLAGS.get(c, ""), "services": list(s)}
            for c, s in COUNTRY_SERVICES.items() if s}


# ---- простой in-memory рейт-лимит на токен (один процесс uvicorn) ----
_hits: dict[int, list] = {}


def _rate_ok(tid: int, per_min: int) -> bool:
    if not per_min:
        return True
    now = time.time()
    arr = [t for t in _hits.get(tid, []) if now - t < 60.0]
    if len(arr) >= per_min:
        _hits[tid] = arr
        return False
    arr.append(now)
    _hits[tid] = arr
    return True


@router.get("/catalog")
async def catalog(tok=Depends(require_token("send"))):
    """Список стран/сервисов, из которых клиент выбирает, куда слать."""
    return {"countries": _catalog()}


@router.get("/send/{job_id}")
async def send_status(job_id: str, tok=Depends(require_token("send"))):
    """Статус отправки по id из ответа /send (только свои отправки этого токена).

    status: `sent` «Отправлено» · `error` «Ошибка отправки» · `pending` «В обработке».
    Неизвестный/чужой id → 404.
    """
    from bot.services.send_status import status_of_job
    st = status_of_job(job_id, tok.id)
    if st is not None:
        return st
    if worker.is_pending(job_id, tok.id):
        return {"id": job_id, "status": "pending", "label": "В обработке"}
    raise HTTPException(404, "unknown job id")


@router.post("/send")
async def send(body: SendIn, tok=Depends(require_token("send"))):
    # техработы (глобальный kill-switch приёма) → 503
    from bot.services.api_flags import is_paused
    if is_paused():
        raise HTTPException(503, "service under maintenance")

    # Telegram-идентификация обязательна (учёт): id (userid, число) + @username
    tg_id = str(body.id or "").strip()
    uname = str(body.username or "").strip().lstrip("@")
    if not tg_id or not uname:
        raise HTTPException(422, "telegram id and username are required")
    if not tg_id.isdigit():
        raise HTTPException(422, "id must be telegram user id (numeric)")

    cat = _catalog()
    # страна/сервис — только из каталога и из разрешённых токену
    if body.country not in cat:
        raise HTTPException(422, "unknown country")
    if body.service not in cat[body.country]["services"]:
        raise HTTPException(422, "unknown service for country")
    if not T.allows_service(tok, body.service):
        raise HTTPException(403, "service not allowed for token")
    # email: проверяем формат, затем исправляем очевидную опечатку домена
    if not _EMAIL_RE.match((body.email or "").strip()):
        raise HTTPException(422, "bad email")
    from bot.services.emailfix import fix_email
    email = fix_email(body.email.strip())
    # ссылка: только http(s) + домен из разрешённых токену
    link = (body.link or "").strip()
    mo = _LINK_RE.match(link)
    if not mo:
        raise HTTPException(422, "link must be http(s)")
    domain = mo.group(1).split(":")[0].lower()
    if not T.allows_link_domain(tok, domain):
        raise HTTPException(403, "link domain not allowed for token")

    # адрес в стоп-листе (отписка/жалоба) → отдельная ошибка 409, не общий 422
    from bot.services.suppression import is_suppressed
    if is_suppressed(email):
        raise HTTPException(409, "recipient is suppressed")

    # собрать payload (рубрика по домену получателя; клиент контент не задаёт)
    from bot.services.send_core import build_payload, SendError
    extra = {}
    if body.price is not None:
        extra["*PRICE*"] = body.price
    if body.predmetname is not None:
        extra["*PNAME*"] = body.predmetname
    try:
        payload = build_payload(
            country=body.country, service=body.service,
            email=email, link=link,
            sender_class="white", extra=extra or None,
        )
    except SendError as e:
        raise HTTPException(422, re.sub(r"<[^>]+>", "", str(e)))  # без HTML-разметки

    # антидубль (наш серверный переключатель, НЕ клиентский): если включён и на этот
    # email уже была успешная отправка за окно — повтор не шлём (решаем мы).
    from bot.services.api_flags import is_dedup, DEDUP_HOURS
    if is_dedup():
        from bot.services.send_core import sent_recently
        if sent_recently(email, DEDUP_HOURS):
            return {"status": "duplicate", "id": None}

    # рейт + суточная квота (после валидации, до постановки в очередь)
    if not _rate_ok(tok.id, tok.rate_per_min):
        raise HTTPException(429, "rate limit exceeded")
    if not T.consume_quota(tok.id, 1):
        raise HTTPException(429, "daily quota exceeded")

    job_id = "snd_" + secrets.token_hex(5)
    ok = await worker.enqueue({
        "job_id": job_id, "payload": payload, "token_id": tok.id,
        "ext_id": tg_id, "ext_username": uname,
    })
    if not ok:
        T.consume_quota(tok.id, -1)  # вернуть квоту — в очередь не попало
        raise HTTPException(503, "queue full, try later")
    return {"id": job_id, "status": "queued"}
