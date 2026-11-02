"""Фоновая очередь + воркер API-процесса (раздел 7 api/README.md).

Приём НЕ шлёт синхронно в HTTP-запросе (иначе запросами выжмут потоки → DoS):
эндпоинт кладёт готовый payload в очередь, а воркер по одному отправляет через
общее ядро `bot.services.send_core.send_payload`. Чтобы клиент узнавал итог без
второго запроса, под каждую задачу создаётся Future: воркер кладёт в него результат,
а хендлер ждёт его `wait_result(...)` до `API_SEND_WAIT_SEC` и возвращает sent/error.
Не успел дождаться → хендлер отдаёт `pending` + id (очередь всё равно доработает).

Один процесс uvicorn (localhost, за CF/nginx) → одна очередь в памяти. Запуск/стоп
воркера — через lifespan приложения (api/app.py).
"""
from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("api")

_queue: "asyncio.Queue | None" = None
_task: "asyncio.Task | None" = None
_MAXSIZE = 1000
# job_id → token_id для задач, что ещё в очереди/в работе (для статуса «в обработке»).
# Один процесс uvicorn → разделяется с роутами. Чистится по завершении задачи.
_pending: dict[str, int] = {}
# job_id → Future[dict] с результатом send_payload (для синхронного ответа /send).
_results: "dict[str, asyncio.Future]" = {}


def is_pending(job_id: str, token_id: int) -> bool:
    """Задача с таким job_id ещё в очереди/в работе И принадлежит этому токену."""
    return _pending.get(job_id) == token_id


async def wait_result(job_id: str, timeout: float) -> "dict | None":
    """Дождаться результата задачи (dict из send_payload) до timeout секунд.

    None — задачи нет, либо не успела за timeout (результат придёт позже, задача не
    отменяется). `shield` не даёт таймауту отменить сам воркерский Future."""
    fut = _results.get(job_id)
    if fut is None or timeout <= 0:
        return None
    try:
        return await asyncio.wait_for(asyncio.shield(fut), timeout=timeout)
    except asyncio.TimeoutError:
        return None


async def _run() -> None:
    from bot.services.send_core import send_payload
    assert _queue is not None
    while True:
        job = await _queue.get()
        jid = job.get("job_id") or ""
        result: dict = {"ok": False, "info": "internal error"}
        try:
            result = await send_payload(
                job["payload"],
                api_token_id=job.get("token_id"),
                ext_id=job.get("ext_id"),
                ext_username=job.get("ext_username"),
                job_id=job.get("job_id"),
            )
            log.info("api send %s token=%s ok=%s", job.get("job_id"), job.get("token_id"), result.get("ok"))
            # квота списана при постановке в очередь — неудачная отправка её возвращает
            # (иначе при лежащих нодах/без аккаунтов клиент «съедал» суточный лимит впустую)
            if not result.get("ok") and job.get("token_id") is not None:
                from bot.services import api_tokens as T
                T.consume_quota(job["token_id"], -1)
        except Exception as e:  # воркер не должен падать из-за одной задачи
            log.exception("api send job %s failed: %s", job.get("job_id"), e)
        finally:
            _pending.pop(jid, None)
            fut = _results.pop(jid, None)
            if fut is not None and not fut.done():
                fut.set_result(result)
            _queue.task_done()


async def start() -> None:
    global _queue, _task
    if _queue is None:
        _queue = asyncio.Queue(maxsize=_MAXSIZE)
    if _task is None or _task.done():
        _task = asyncio.create_task(_run())
        log.info("api send worker started")


async def stop(drain_timeout: float = 30.0) -> None:
    """Graceful-shutdown: сперва дожидаемся, пока воркер доработает очередь
    (новых запросов уже нет — FastAPI остановил приём), затем отменяем задачу.
    Так при остановке API не теряем поставленные/выполняемые отправки."""
    global _task
    if _queue is not None:
        try:
            await asyncio.wait_for(_queue.join(), timeout=drain_timeout)
        except asyncio.TimeoutError:
            log.warning("api send worker: очередь не допроработала за %ss — отменяю", drain_timeout)
    if _task is not None:
        _task.cancel()
        _task = None


async def enqueue(job: dict) -> bool:
    """Положить задачу в очередь. False — очередь не готова или переполнена.

    Под задачу заводится Future (до постановки в очередь, чтобы воркер его нашёл) —
    по нему `wait_result` отдаёт синхронный итог. Переполнение → Future не остаётся."""
    if _queue is None:
        return False
    jid, tid = job.get("job_id"), job.get("token_id")
    fut = asyncio.get_event_loop().create_future() if jid else None
    try:
        _queue.put_nowait(job)
    except asyncio.QueueFull:
        return False
    if jid and tid is not None:
        _pending[jid] = tid
    if jid and fut is not None:
        _results[jid] = fut
    return True
