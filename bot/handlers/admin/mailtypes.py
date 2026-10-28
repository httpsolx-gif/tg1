import logging
import re
from typing import List

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from common.config import settings
from bot.fsm.admin import MailboxAdd, MailboxDomains
from bot.services import mailboxes as mb

router = Router(name="mailtypes-admin")
log = logging.getLogger("bot")


def kb_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="+ Добавить", callback_data="mb:add"),
            InlineKeyboardButton(text="📋 Список", callback_data="mb:list"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="mb:back")],
    ])


def _kb_add_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="mb:root")]])


def _kb_list() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for m in mb.all_mailboxes():
        if m.is_builtin:
            rows.append([InlineKeyboardButton(text=f"🔒 {m.code} · {m.name}", callback_data="mb:noop")])
        else:
            rows.append([InlineKeyboardButton(text=f"🗑 {m.code} · {m.name}", callback_data=f"mb:del:{m.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="mb:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def show_mailtypes_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    codes = ", ".join(f"{c}={n}" for c, n in mb.pairs())
    await m.answer(
        "📮 ТИПЫ ПОЧТ (рубрики почт).\n\n"
        "Используются в именах шаблонов (myemail.pl-<b>код</b>.html), в почтах рубрик и как категория SMTP.\n\n"
        f"Сейчас: {codes}",
        reply_markup=kb_root(),
    )


@router.callback_query(F.data == "mb:root")
async def mb_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    codes = ", ".join(f"{c}={n}" for c, n in mb.pairs())
    try:
        await cq.message.edit_text(f"📮 ТИПЫ ПОЧТ.\n\nСейчас: {codes}", reply_markup=kb_root())
    except Exception:
        await cq.message.answer(f"📮 ТИПЫ ПОЧТ.\n\nСейчас: {codes}", reply_markup=kb_root())
    await cq.answer()


@router.callback_query(F.data == "mb:back")
async def mb_back(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    from bot.handlers.admin.index import back_to_admin
    await back_to_admin(cq)


@router.callback_query(F.data == "mb:add")
async def mb_add(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.message.answer(
        "Добавление типа почты.\n\n"
        "Формат: <code>код имя</code> (напр. <code>p protonmail</code> или <code>i=icloud</code>).\n"
        "Код — для имён файлов шаблонов.",
        reply_markup=_kb_add_cancel(),
    )
    await state.set_state(MailboxAdd.waiting)
    await cq.answer()


@router.message(MailboxAdd.waiting, F.text)
async def mb_add_text(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    parts = re.split(r"[\s:=;,|]+", (m.text or "").strip(), maxsplit=1)
    if len(parts) < 2:
        await m.answer("Нужно: <code>код имя</code>", reply_markup=_kb_add_cancel())
        return
    try:
        obj = mb.add_mailbox(parts[0], parts[1])
    except ValueError as e:
        await m.answer(f"❌ {e}", reply_markup=_kb_add_cancel())
        return
    await state.clear()
    await m.answer(f"✅ Добавлен тип почты: <b>{obj.code}</b> · {obj.name}", reply_markup=kb_root())


@router.callback_query(F.data == "mb:list")
async def mb_list(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        await cq.message.edit_text("📮 Типы почт (🔒 встроенные, 🗑 — удалить кастомные):", reply_markup=_kb_list())
    except Exception:
        await cq.message.answer("📮 Типы почт:", reply_markup=_kb_list())
    await cq.answer()


@router.callback_query(F.data.startswith("mb:del:"))
async def mb_del(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        mid = int(cq.data.split(":")[-1])
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return
    mb.remove_mailbox(mid)
    await cq.answer("Удалён")
    await cq.message.edit_text("📮 Типы почт (🔒 встроенные, 🗑 — удалить кастомные):", reply_markup=_kb_list())


@router.callback_query(F.data == "mb:noop")
async def mb_noop(cq: CallbackQuery):
    await cq.answer("Встроенный тип — не удаляется")
