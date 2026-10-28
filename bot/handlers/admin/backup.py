"""BACKUP — выгрузка всех БД и бэкап в Telegram-канал. Колбэки `bk:`.

«Выгрузить все БД» — дамп Postgres + clicks.db со всех shorter-нод, файлы
приходят прямо в чат. «Бэкап в канал» — дамп в BACKUP_CHANNEL_ID (оффсайт).
Авто-бэкап по расписанию живёт в main.py (`backup_loop`), период — BACKUP_EVERY_HOURS.
"""
from __future__ import annotations

import logging

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile

from common.config import settings

router = Router(name="backup-admin")
log = logging.getLogger("bot")


def _admin(uid: int) -> bool:
    return uid in settings.admin_ids_set


def _btn(t: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t, callback_data=cb)


def kb_backup_root() -> InlineKeyboardMarkup:
    hours = int(getattr(settings, "BACKUP_EVERY_HOURS", 0) or 0)
    chan = (settings.BACKUP_CHANNEL_ID or "").strip()
    auto = f"каждые {hours}ч → канал" if (hours and chan) else "выкл (BACKUP_CHANNEL_ID/HOURS)"
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("📦 Выгрузить все БД (в чат)", "bk:export")],
        [_btn("💾 Бэкап в канал сейчас", "bk:channel")],
        [_btn(f"🔁 Авто-бэкап: {auto}", "bk:info")],
        [_btn("⬅️ Назад", "admin:back")],
    ])


async def show_backup_root(m: Message):
    if not _admin(m.from_user.id):
        return
    await m.answer("💾 <b>BACKUP</b> — резервные копии и выгрузка БД:", reply_markup=kb_backup_root())


@router.callback_query(F.data == "bk:root")
async def bk_root(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        await cq.message.edit_text("💾 <b>BACKUP</b> — резервные копии и выгрузка БД:", reply_markup=kb_backup_root())
    except Exception:
        await cq.message.answer("💾 <b>BACKUP</b> — резервные копии и выгрузка БД:", reply_markup=kb_backup_root())
    await cq.answer()


@router.callback_query(F.data == "bk:info")
async def bk_info(cq: CallbackQuery):
    await cq.answer(
        "Авто-бэкап: задать BACKUP_CHANNEL_ID (бот — админ канала) и BACKUP_EVERY_HOURS в .env, перезапустить бота.",
        show_alert=True)


@router.callback_query(F.data == "bk:export")
async def bk_export(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.answer("Готовлю дампы…")
    await cq.message.answer("⏳ Делаю дамп БД и собираю clicks.db с нод…")
    from bot.services.backup import full_export
    try:
        files = await full_export()
    except Exception as e:
        await cq.message.answer(f"🔴 Ошибка выгрузки: <code>{str(e)[:250]}</code>")
        return
    for p in files:
        try:
            await cq.message.answer_document(FSInputFile(str(p)), caption=p.name)
        except Exception as e:
            await cq.message.answer(f"🔴 Не отправился {p.name}: <code>{str(e)[:150]}</code>")
    await cq.message.answer(f"✅ Готово: {len(files)} файл(ов).", reply_markup=kb_backup_root())


@router.callback_query(F.data == "bk:channel")
async def bk_channel(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.answer("Бэкаплю в канал…")
    from bot.services.backup import backup_to_channel
    ok, info = await backup_to_channel(cq.message.bot)
    await cq.message.answer(
        (f"✅ Бэкап отправлен в канал: <code>{info}</code>" if ok else f"🔴 {info}"),
        reply_markup=kb_backup_root())
