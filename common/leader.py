"""Лидер-лок через pg_advisory_lock (п.57): основной + резервный экземпляр.

Запускаем один и тот же процесс (бот / userbot) на двух серверах. Каждый при старте
пытается взять advisory-лок в общей Postgres:
  • взял → работает (polling, фоновые циклы);
  • не взял → стоит в резерве и раз в LEADER_RETRY_SEC пробует снова.

Лок привязан к ОТДЕЛЬНОМУ соединению (не из пула SQLAlchemy). Умер процесс/сервер →
соединение рвётся → Postgres отпускает лок → резерв подхватывает.

Фенсинг (защита от двух лидеров): лидер раз в LEADER_HEARTBEAT_SEC проверяет, что
соединение живо и лок ещё его. Если нет — процесс немедленно завершается (os._exit(3)),
systemd (Restart=always) поднимает его заново, и он встаёт в резерв. Именно выход,
а не мягкая остановка: потоки в executor'ах (SMTP/IMAP) не отменяются, а жёсткий
выход гарантирует, что старый лидер ничего не доделает параллельно с новым.

Тайминги: лидер замечает потерю за ≤ HEARTBEAT×2 (~10с); сервер отпускает лок мёртвого
клиента по TCP keepalive (~25с) → старый лидер успевает остановиться раньше, чем
резерв возьмёт лок.

На sqlite (dev/смоуки) и при LEADER_LOCK=0 лок не берётся — процесс сразу лидер.
"""
from __future__ import annotations

import asyncio
import logging
import os
import socket

from common.config import settings

log = logging.getLogger("leader")

# TCP keepalive в обе стороны: клиент быстро видит обрыв, сервер быстро отпускает лок
# tcp_user_timeout: если обрыв случился с неподтверждёнными данными в сокете, keepalive не
# работает (идут ретрансмиты ~15 мин) — user_timeout ограничивает и этот случай (~20с).
# ⚠️ Только прямое подключение к Postgres: pgbouncer отвергает эти options, а в transaction-
# режиме ещё и ломает сессионные advisory-локи.
_KEEPALIVE = dict(keepalives=1, keepalives_idle=10, keepalives_interval=5, keepalives_count=3,
                  tcp_user_timeout=20000)
_SERVER_KEEPALIVE = ("-c tcp_keepalives_idle=10 -c tcp_keepalives_interval=5 -c tcp_keepalives_count=3"
                     " -c tcp_user_timeout=20000")


def _dsn() -> str | None:
    """DATABASE_URL в формате libpq (без '+psycopg'); None — не Postgres."""
    url = settings.DATABASE_URL or ""
    if not url.startswith("postgres"):
        return None
    return "postgresql://" + url.split("://", 1)[1]


class LeaderLock:
    def __init__(self, name: str):
        self.name = name                      # ключ лока, напр. "tg1:bot"
        self.instance = socket.gethostname()  # для логов и pg_stat_activity
        self._conn = None
        self._task: asyncio.Task | None = None

    async def _connect(self):
        import psycopg
        return await psycopg.AsyncConnection.connect(
            _dsn(),
            autocommit=True,
            connect_timeout=10,
            application_name=f"leader:{self.name}@{self.instance}"[:63],
            options=_SERVER_KEEPALIVE,
            **_KEEPALIVE,
        )

    async def _close(self) -> None:
        if self._conn is not None:
            try:
                await self._conn.close()
            except Exception:
                pass
            self._conn = None

    async def acquire(self) -> None:
        """Блокируется, пока этот экземпляр не станет лидером."""
        if _dsn() is None or not settings.LEADER_LOCK:
            log.info("leader lock %s: выключен (sqlite или LEADER_LOCK=0) — работаю без резерва", self.name)
            return
        waited = False
        while True:
            got = False
            try:
                if self._conn is None or self._conn.closed:
                    self._conn = await self._connect()
                cur = await self._conn.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (self.name,))
                got = bool((await cur.fetchone())[0])
            except Exception as e:
                log.warning("leader lock %s: БД недоступна (%s) — повтор через %ss",
                            self.name, e, settings.LEADER_RETRY_SEC)
                await self._close()
            if got:
                break
            if not waited:
                log.info("🟡 %s [%s]: лидер уже есть — стою в РЕЗЕРВЕ", self.name, self.instance)
                waited = True
            await asyncio.sleep(settings.LEADER_RETRY_SEC)
        if waited:
            # WARNING → уйдёт и в ERR-канал: видно, что произошло переключение
            log.warning("🟢 %s [%s]: лидер пропал — ПЕРЕХВАТИЛ работу", self.name, self.instance)
        else:
            log.info("🟢 %s [%s]: я лидер", self.name, self.instance)

    async def _still_held(self) -> bool:
        cur = await self._conn.execute(
            "SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND pid = pg_backend_pid() AND granted"
        )
        return await cur.fetchone() is not None

    async def _watchdog(self) -> None:
        period = settings.LEADER_HEARTBEAT_SEC
        while True:
            await asyncio.sleep(period)
            try:
                ok = await asyncio.wait_for(self._still_held(), timeout=period)
                reason = "лок больше не наш"
            except Exception as e:
                ok, reason = False, f"нет связи с БД: {e!r}"
            if not ok:
                log.error("🔴 %s [%s]: лидерство потеряно (%s) — аварийный выход, systemd перезапустит в резерв",
                          self.name, self.instance, reason)
                logging.shutdown()
                os._exit(3)

    def start_watchdog(self) -> None:
        """Запустить фенсинг после acquire(). Без лока (sqlite) — ничего не делает."""
        if self._conn is not None and self._task is None:
            self._task = asyncio.create_task(self._watchdog())
