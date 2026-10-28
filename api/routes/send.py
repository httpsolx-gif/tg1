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

from fastapi import APIRouter, Depends, HTTPException, Header

from pydantic import BaseModel

from api.auth import require_token
from api import worker
from bot.services import api_tokens as T
from bot.services import idempotency

router = APIRouter(prefix="/api/v1", tags=["send"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_LINK_RE = re.compile(r"^https?://([^/\s]+)", re.I)


class SendIn(BaseModel):
    country: str
    service: str
    email: str
    link: str
    price: Optional[str] = None          # → *PRICE* в шаблоне
    predmetname: Optional[str] = None    # → *PNAME* в шаблоне
    id: Optional[str] = None             # клиентский id — только в лог
    username: Optional[str] = None       # клиентский username — только в лог


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


@router.post("/send")
async def send(body: SendIn, tok=Depends(require_token("send")),
               idempotency_key: str | None = Header(default=None, alias="Idempotency-Key")):
    # идемпотентность: повтор с тем же ключом (в рамках токена) → прежний ответ
    if idempotency_key:
        prev = idempotency.get(tok.id, idempotency_key)
        if prev is not None:
            return prev

    cat = _catalog()
    # страна/сервис — только из каталога и из разрешённых токену
    if body.country not in cat:
        raise HTTPException(422, "unknown country")
    if body.service not in cat[body.country]["services"]:
        raise HTTPException(422, "unknown service for country")
    if not T.allows_service(tok, body.service):
        raise HTTPException(403, "service not allowed for token")
    # email
    if not _EMAIL_RE.match((body.email or "").strip()):
        raise HTTPException(422, "bad email")
    # ссылка: только http(s) + домен из разрешённых токену
    link = (body.link or "").strip()
    mo = _LINK_RE.match(link)
    if not mo:
        raise HTTPException(422, "link must be http(s)")
    domain = mo.group(1).split(":")[0].lower()
    if not T.allows_link_domain(tok, domain):
        raise HTTPException(403, "link domain not allowed for token")

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
            email=body.email.strip(), link=link,
            sender_class="white", extra=extra or None,
        )
    except SendError as e:
        raise HTTPException(422, re.sub(r"<[^>]+>", "", str(e)))  # без HTML-разметки

    # рейт + суточная квота (после валидации, до постановки в очередь)
    if not _rate_ok(tok.id, tok.rate_per_min):
        raise HTTPException(429, "rate limit exceeded")
    if not T.consume_quota(tok.id, 1):
        raise HTTPException(429, "daily quota exceeded")

    job_id = "snd_" + secrets.token_hex(5)
    ok = await worker.enqueue({
        "job_id": job_id, "payload": payload, "token_id": tok.id,
        "ext_id": body.id, "ext_username": body.username,
    })
    if not ok:
        T.consume_quota(tok.id, -1)  # вернуть квоту — в очередь не попало
        raise HTTPException(503, "queue full, try later")
    resp = {"id": job_id, "status": "queued"}
    if idempotency_key:
        idempotency.save(tok.id, idempotency_key, resp)  # запомнить ответ под ключом
    return resp
