"""Выбор ноды отправки с переключением на резервную (п.59).

Порядок кандидатов: основные ноды (по id), затем резервные (`Node.is_backup`).
Нода не ответила по SSH (NODE_DOWN из node_runner) → помечаем «лежит» на
DOWN_COOLDOWN_SEC и сразу пробуем следующую. Пока метка висит, нода уходит в конец
очереди, чтобы каждое письмо не ждало таймаут подключения. Метка истекла → нода
снова первая по порядку: основная сама возвращается в работу, когда поднимется.

Переходы «упала» / «снова в строю» пишутся WARNING'ом → уходят в ERR-канал.
Состояние в памяти процесса (у бота и API — своё), после рестарта всё снова «живое».
"""
from __future__ import annotations

import logging
import time
from typing import Awaitable, Callable, Optional, Tuple

from sqlmodel import Session, select

from common.db import get_engine
from common.models.node import Node
from bot.services.node_runner import NODE_DOWN, is_node_down

log = logging.getLogger("bot")
_engine = get_engine()

DOWN_COOLDOWN_SEC = 120
_down: dict[int, float] = {}  # node_id → time.time(), когда упала


def is_down(node_id: int) -> bool:
    ts = _down.get(node_id)
    return ts is not None and time.time() - ts < DOWN_COOLDOWN_SEC


def down_since(node_id: int) -> Optional[float]:
    return _down.get(node_id) if is_down(node_id) else None


def _label(n: Node) -> str:
    return f"{n.login}@{n.ip}" + (" (резерв)" if n.is_backup else "")


def _order(nodes: list[Node]) -> list[Node]:
    """Основные раньше резервных; «лежащие» — в самый конец (на случай, если живых нет)."""
    return sorted(nodes, key=lambda n: (is_down(n.id), bool(n.is_backup), n.id))


def send_nodes() -> list[Node]:
    """Кандидаты для SMTP: активные ноды с ролью отправки."""
    with Session(_engine) as db:
        nodes = db.exec(select(Node).where(Node.is_active == True)).all()  # noqa: E712
    return _order([n for n in nodes if n.has_role("send")])


def ses_nodes(aws) -> list[Node]:
    """Кандидаты для SES: ноды с регионом аккаунта, плюс явная привязка aws.node_id."""
    reg = (aws.region or "").strip().lower()
    with Session(_engine) as db:
        nodes = db.exec(select(Node).where(Node.is_active == True)).all()  # noqa: E712
    picked = [n for n in nodes if reg and (n.region or "").strip().lower() == reg]
    if aws.node_id and all(n.id != aws.node_id for n in picked):
        picked += [n for n in nodes if n.id == aws.node_id]
    return _order(picked)


def _mark_down(n: Node, info: str) -> None:
    # алерт только на переход «работала → упала»; запись живёт до _mark_up,
    # чтобы повторные проверки после кулдауна не спамили в ERR-канал
    fresh = n.id not in _down
    _down[n.id] = time.time()
    try:
        with Session(_engine) as db:
            row = db.get(Node, n.id)
            if row:
                row.error_count = (row.error_count or 0) + 1
                db.add(row)
                db.commit()
    except Exception as e:
        log.warning("node error_count: %s", e)
    if fresh:
        log.warning("🔴 Нода %s не отвечает — переключаюсь на следующую (%s)", _label(n), info[:200])


def _mark_up(n: Node) -> None:
    if _down.pop(n.id, None) is not None:
        log.warning("🟢 Нода %s снова отвечает — вернулась в работу", _label(n))


SendFn = Callable[[Node], Awaitable[Tuple[bool, str]]]


async def send_with_failover(nodes: list[Node], fn: SendFn) -> Tuple[bool, str, Optional[Node], bool]:
    """Пробовать ноды по очереди, пока одна не ответит.

    Возвращает (ok, info, нода_которая_ответила | None, все_лежат).
    Переключаемся ТОЛЬКО на «нода не отвечает»; ошибка SMTP/SES на живой ноде —
    это ответ, его возвращаем как есть (другая нода дала бы ту же ошибку или дубль).
    """
    failed: list[str] = []
    for n in nodes:
        ok, info = await fn(n)
        if is_node_down(info):
            _mark_down(n, info)
            failed.append(f"{_label(n)}: {info[len(NODE_DOWN):]}")
            continue
        _mark_up(n)
        return ok, info, n, False
    return False, ("все ноды отправки недоступны — " + "; ".join(failed)) if failed else "нет нод", None, True
