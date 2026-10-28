"""Фоновая очередь + воркер API-процесса (раздел 7 api/README.md).

Приём НЕ шлёт синхронно в HTTP-запросе (иначе запросами выжмут потоки → DoS):
эндпоинт кладёт готовый payload в очередь и сразу отвечает `queued`, а воркер
по одному отправляет через общее ядро `bot.services.send_core.send_payload`.

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


async def _run() -> None:
    from bot.services.send_core import send_payload
    assert _queue is not None
    while True:
        job = await _queue.get()
        try:
            res = await send_payload(
                job["payload"],
                api_token_id=job.get("token_id"),
                ext_id=job.get("ext_id"),
                ext_username=job.get("ext_username"),
            )
            log.info("api send %s token=%s ok=%s", job.get("job_id"), job.get("token_id"), res.get("ok"))
        except Exception as e:  # воркер не должен падать из-за одной задачи
            log.exception("api send job %s failed: %s", job.get("job_id"), e)
        finally:
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
    """Положить задачу в очередь. False — очередь не готова или переполнена."""
    if _queue is None:
        return False
    try:
        _queue.put_nowait(job)
        return True
    except asyncio.QueueFull:
        return False
