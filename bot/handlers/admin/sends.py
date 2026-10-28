"""Выгрузка базы email в формате сервис:email (инкрементально, с курсором).

/sends — выгрузить новые успешные адреса с прошлой выгрузки в .txt (формат
         `сервис:email`, дубли убраны), курсор сдвигается на последний id.
/clast — сбросить курсор (следующая /sends выгрузит ВСЮ базу заново).

Админ-онли. Курсор — AppState['sends_cursor'] = последний экспортированный SendLog.id.
Выгружаются только успешные отправки (status='ok').
"""
from __future__ import annotations

import logging

from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message, BufferedInputFile
from sqlmodel import Session, select

from common.db import get_engine
from common.config import settings
from common.models.send_log import SendLog
from common.models.appstate import AppState

router = Router(name="sends-admin")
log = logging.getLogger("bot")
_engine = get_engine()

_CURSOR_KEY = "sends_cursor"


def _admin(uid: int) -> bool:
    return uid in settings.admin_ids_set


def _get_cursor() -> int:
    with Session(_engine) as db:
        s = db.get(AppState, _CURSOR_KEY)
    try:
        return int(s.value) if s and s.value else 0
    except (TypeError, ValueError):
        return 0


def _set_cursor(val: int) -> None:
    with Session(_engine) as db:
        s = db.get(AppState, _CURSOR_KEY)
        if not s:
            s = AppState(key=_CURSOR_KEY, value=str(val))
        else:
            s.value = str(val)
        db.add(s)
        db.commit()


@router.message(Command("sends"))
async def cmd_sends(m: Message):
    if not _admin(m.from_user.id):
        return
    cursor = _get_cursor()
    with Session(_engine) as db:
        rows = db.exec(
            select(SendLog)
            .where((SendLog.id > cursor) & (SendLog.status == "ok"))
            .order_by(SendLog.id)
        ).all()
    if not rows:
        await m.answer(
            f"Нет новых адресов с последней выгрузки (курсор id={cursor}).\n"
            "/clast — сбросить курсор и выгрузить всю базу заново.")
        return
    max_id = rows[-1].id
    seen: set[str] = set()
    lines: list[str] = []
    for r in rows:
        email = (r.email or "").strip()
        if not email:
            continue
        svc = (r.service or "—").strip()
        line = f"{svc}:{email}"
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    _set_cursor(max_id)
    if not lines:
        await m.answer(f"Новые отправки есть, но без email — выгружать нечего. Курсор → id={max_id}.")
        return
    body = "\n".join(lines) + "\n"
    await m.answer_document(
        BufferedInputFile(body.encode(), filename="emails_base.txt"),
        caption=(f"📤 База email (сервис:email): {len(lines)} шт "
                 f"(новые с id>{cursor}, дубли убраны). Курсор → id={max_id}."))


@router.message(Command("clast"))
async def cmd_clast(m: Message):
    if not _admin(m.from_user.id):
        return
    old = _get_cursor()
    _set_cursor(0)
    await m.answer(f"♻️ Курсор выгрузки сброшен (был id={old}). Следующая /sends выгрузит ВСЮ базу.")
