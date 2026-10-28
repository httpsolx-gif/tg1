"""USERS — списки доступа отправителей: ban / white / black (п.43).

- 🚫 BAN — полностью блокирует отправку.
- ✅ White — обычная отправка (и возврат из black/ban в норму).
- ⚫️ Black — отправка через отдельный профиль (SMTP → BLACK).

Правило: админ → white; ban → блок; white → обычная; иначе (black или нигде) → black.
Записи — числовой id или @username (сверяем оба при отправке). Колбэки `usr:`.
Сам профиль black (шаблон/отправитель/тайтл/сокращатель/SMTP) — в разделе SMTP → BLACK.
"""
from __future__ import annotations

import logging
from html import escape as _esc

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext

from common.config import settings
from bot.fsm.admin import AccessAdd
from bot.services import access

router = Router(name="users-admin")
log = logging.getLogger("bot")


def _admin(uid: int) -> bool:
    return uid in settings.admin_ids_set


def _btn(t: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t, callback_data=cb)


def kb_users_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🚫 BAN", "usr:lst:ban")],
        [_btn("✅ White", "usr:lst:white")],
        [_btn("⚫️ Black", "usr:lst:black")],
        [_btn("⬅️ Назад", "admin:back")],
    ])


_HINT = {
    "ban": "🚫 <b>BAN</b> — этим пользователям отправка полностью запрещена.",
    "white": "✅ <b>White</b> — обычная отправка (можно вернуть юзера из black/ban).",
    "black": "⚫️ <b>Black</b> — отправка через отдельный профиль (SMTP → BLACK).\n"
             "<i>Не в white и не в ban = тоже black.</i>",
}


async def show_users_root(m: Message):
    if not _admin(m.from_user.id):
        return
    await m.answer("👥 <b>USERS</b> — списки доступа отправителей:", reply_markup=kb_users_root())


@router.callback_query(F.data == "usr:root")
async def usr_root(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("👥 <b>USERS</b> — списки доступа отправителей:", reply_markup=kb_users_root())
    except Exception:
        await cq.message.answer("👥 <b>USERS</b> — списки доступа отправителей:", reply_markup=kb_users_root())
    await cq.answer()


def _kb_list(lst: str) -> InlineKeyboardMarkup:
    rows = []
    for e in access.list_entries(lst):
        tag = "" if e.is_id else "@"
        rows.append([_btn(f"🗑 {tag}{e.value}", f"usr:del:{e.id}:{lst}")])
    rows.append([_btn("+ Добавить", f"usr:add:{lst}")])
    rows.append([_btn("⬅️ Назад", "usr:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _show_list(target, lst: str, *, edit: bool):
    entries = access.list_entries(lst)
    head = _HINT.get(lst, lst)
    body = f"\n\n<b>В списке ({len(entries)}):</b>\n" + (
        "\n".join(f"• {'' if e.is_id else '@'}{_esc(e.value)}" for e in entries) if entries else "<i>пусто</i>")
    text = head + body
    kb = _kb_list(lst)
    if edit:
        try:
            await target.edit_text(text, reply_markup=kb)
            return
        except Exception:
            pass
        await target.answer(text, reply_markup=kb)
    else:
        await target.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("usr:lst:"))
async def usr_list(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    lst = cq.data.split(":")[2]
    await _show_list(cq.message, lst, edit=True)
    await cq.answer()


@router.callback_query(F.data.startswith("usr:add:"))
async def usr_add(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    lst = cq.data.split(":")[2]
    await state.set_state(AccessAdd.waiting)
    await state.update_data(lst=lst)
    await cq.message.answer(
        f"Пришлите id и/или @username для списка <b>{access.LIST_RU.get(lst, lst)}</b> "
        "(можно пачкой через пробел/запятую/перенос):")
    await cq.answer()


@router.message(AccessAdd.waiting, F.text)
async def usr_add_text(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    data = await state.get_data()
    lst = data.get("lst", "ban")
    await state.clear()
    added, skipped = access.add_entries(lst, m.text or "")
    if not added and not skipped:
        await m.answer("Пусто — ничего не добавил. Пришлите id/@username ещё раз или вернитесь в меню.")
        return
    note = f"✅ Добавлено: {added}" + (f", пропущено (уже были): {skipped}" if skipped else "")
    await m.answer(note)
    await _show_list(m, lst, edit=False)


@router.callback_query(F.data.startswith("usr:del:"))
async def usr_del(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    parts = cq.data.split(":")
    entry_id = int(parts[2])
    lst = parts[3] if len(parts) > 3 else "ban"
    access.remove_entry(entry_id)
    await cq.answer("Удалено")
    await _show_list(cq.message, lst, edit=True)
