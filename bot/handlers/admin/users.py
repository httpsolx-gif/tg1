"""USERS — доступ отправителей (ban/white/black) + поиск-карточка по id (п.43).

Корень: быстрые действия Ban/Unban/White/Black (ввод id/@username пачкой),
🔍 Поиск по id (карточка юзера: username, отправки, дубли адресов, переходы,
разбивка по почтам в %, + те же кнопки действий на найденного), 📤 Выгрузить
списки, 📋 Списки (просмотр/удаление по записи).

Правило классификации (bot/services/access.classify): админ→white; ban→блок;
white→обычная; иначе (явный black ИЛИ нигде)→black. Профиль black — рубрика
«black» (🧩 Сервисы). Колбэки `usr:`.
"""
from __future__ import annotations

import logging
from html import escape as _esc

from aiogram import Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, BufferedInputFile,
)
from aiogram.fsm.context import FSMContext

from common.config import settings
from bot.fsm.admin import AccessAdd, UserFlow
from bot.services import access

router = Router(name="users-admin")
log = logging.getLogger("bot")

_CLASS_RU = {"ban": "🚫 BAN", "white": "✅ White", "black": "⚫️ Black"}
_ACT_RU = {"ban": "БАН", "white": "WHITE", "black": "BLACK", "unban": "РАЗБАН"}


def _admin(uid: int) -> bool:
    return uid in settings.admin_ids_set


def _btn(t: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t, callback_data=cb)


# ----------------------------- корень -----------------------------
def kb_users_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🚫 Ban", "usr:act:ban"), _btn("♻️ Unban", "usr:act:unban")],
        [_btn("✅ White", "usr:act:white"), _btn("⚫️ Black", "usr:act:black")],
        [_btn("🔍 Поиск по id", "usr:find")],
        [_btn("📤 Выгрузить", "usr:export"), _btn("📋 Списки", "usr:lists")],
        [_btn("⬅️ Назад", "admin:back")],
    ])


_ROOT_TEXT = (
    "👥 <b>USERS</b> — доступ отправителей.\n\n"
    "• <b>Ban</b> — отправка полностью запрещена.\n"
    "• <b>Unban</b> — снять бан (станет black, если не в white).\n"
    "• <b>White</b> — обычная отправка. <b>Black</b> — профиль black (рубрика «black»).\n"
    "• <b>Поиск по id</b> — карточка юзера со статистикой.\n"
    "<i>Идентификация по Telegram id ИЛИ @username (сверяем оба). "
    "Не в white и не в ban = тоже black.</i>"
)


async def show_users_root(m: Message):
    if not _admin(m.from_user.id):
        return
    await m.answer(_ROOT_TEXT, reply_markup=kb_users_root())


@router.callback_query(F.data == "usr:root")
async def usr_root(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text(_ROOT_TEXT, reply_markup=kb_users_root())
    except Exception:
        await cq.message.answer(_ROOT_TEXT, reply_markup=kb_users_root())
    await cq.answer()


# ----------------------------- действия ban/white/black/unban (ввод) -----------------------------
@router.callback_query(F.data.startswith("usr:act:"))
async def usr_act(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    mode = cq.data.split(":")[2]  # ban | white | black | unban
    await state.set_state(UserFlow.act)
    await state.update_data(mode=mode)
    await cq.message.answer(
        f"Пришлите id и/или @username для <b>{_ACT_RU.get(mode, mode)}</b> "
        "(можно пачкой через пробел/запятую/перенос):")
    await cq.answer()


@router.message(UserFlow.act, F.text)
async def usr_act_text(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    mode = (await state.get_data()).get("mode", "ban")
    await state.clear()
    if mode == "unban":
        n = access.unban_bulk(m.text or "")
        await m.answer(f"♻️ Разбанено: {n}.")
    elif mode in ("ban", "white", "black"):
        n = access.set_class_bulk(m.text or "", mode)
        await m.answer(f"✅ В {_ACT_RU[mode]}: {n}.")
    else:
        await m.answer("Неизвестное действие.")
    await m.answer(_ROOT_TEXT, reply_markup=kb_users_root())


# ----------------------------- поиск по id → карточка -----------------------------
@router.callback_query(F.data == "usr:find")
async def usr_find(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(UserFlow.search)
    await cq.message.answer("Пришлите <b>id</b> (или @username) юзера для карточки:")
    await cq.answer()


@router.message(UserFlow.search, F.text)
async def usr_find_text(m: Message, state: FSMContext):
    if not _admin(m.from_user.id):
        return
    await state.clear()
    uid = access.resolve_uid(m.text or "")
    if uid is None:
        await m.answer("Не нашёл такого юзера. Пришлите числовой id (или @username, если он уже слал).")
        await m.answer(_ROOT_TEXT, reply_markup=kb_users_root())
        return
    await m.answer("⏳ Считаю…")
    text, kb = await _card(uid)
    await m.answer(text, reply_markup=kb)


def _kb_card(uid: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🚫 Ban", f"usr:set:ban:{uid}"), _btn("♻️ Unban", f"usr:set:unban:{uid}")],
        [_btn("✅ White", f"usr:set:white:{uid}"), _btn("⚫️ Black", f"usr:set:black:{uid}")],
        [_btn("🔄 Обновить", f"usr:card:{uid}")],
        [_btn("⬅️ Назад", "usr:root")],
    ])


async def _card(uid: int) -> tuple[str, InlineKeyboardMarkup]:
    uname = access.get_username(uid)
    cls = access.classify(uid, uname, settings.admin_ids_set)
    rep = await access.user_report(uid)
    sok, stot = rep["sends_ok"], rep["sends_total"]
    pct = (lambda c, s: f"{(100.0 * c / s):.1f}%" if s else "—")
    # разбивка по почтам в %
    buckets = rep["buckets"]
    tot_b = sum(buckets.values())
    if tot_b:
        parts = sorted(buckets.items(), key=lambda kv: kv[1], reverse=True)
        mail_lines = "\n".join(
            f"   • {_esc(str(k))} — {(100.0 * v / tot_b):.0f}% ({v})" for k, v in parts)
    else:
        mail_lines = "   <i>нет отправок</i>"
    admin_note = " <i>(админ)</i>" if uid in settings.admin_ids_set else ""
    text = (
        f"👤 Юзер <code>{uid}</code> · {('@' + uname) if uname else '—'}\n"
        f"Класс: <b>{_CLASS_RU.get(cls, cls)}</b>{admin_note}\n\n"
        f"• Отправок: <b>{sok}</b> ok из {stot}\n"
        f"• Переходов: <b>{rep['clicks']}</b> (конв {pct(rep['clicks'], sok)})\n"
        f"• Повторные адреса: <b>{rep['dup_addrs']}</b> (лишних отправок {rep['dup_extra']}) "
        f"из {rep['uniq_addrs']} уникальных\n"
        f"• По почтам (%):\n{mail_lines}\n\n"
        "<i>Переходы считаются только по личным short-доменам.</i>"
    )
    return text, _kb_card(uid)


@router.callback_query(F.data.startswith("usr:card:"))
async def usr_card(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    uid = int(cq.data.split(":")[2])
    await cq.answer("Считаю…")
    text, kb = await _card(uid)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("usr:set:"))
async def usr_set(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, mode, uid_s = cq.data.split(":", 3)
    uid = int(uid_s)
    uname = access.get_username(uid)
    if mode == "unban":
        access.unban_user(uid, uname)
        note = "Разбанен"
    else:
        access.set_class_user(uid, uname, mode)
        note = f"→ {_ACT_RU.get(mode, mode)}"
    text, kb = await _card(uid)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer(note)


# ----------------------------- выгрузка списков -----------------------------
@router.callback_query(F.data == "usr:export")
async def usr_export(cq: CallbackQuery):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    blocks = []
    counts = []
    for lst in access.LISTS:
        ents = access.list_entries(lst)
        counts.append(f"{access.LIST_RU[lst]}: {len(ents)}")
        lines = [f"# {lst.upper()}"] + [
            f"{'' if e.is_id else '@'}{e.value}" for e in ents]
        blocks.append("\n".join(lines))
    body = "\n\n".join(blocks) + "\n"
    await cq.message.answer_document(
        BufferedInputFile(body.encode(), filename="access_lists.txt"),
        caption="📤 Списки доступа\n" + " · ".join(counts))
    await cq.answer("Выгружено")


# ----------------------------- списки (просмотр/удаление) -----------------------------
def kb_lists_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [_btn("🚫 BAN", "usr:lst:ban")],
        [_btn("✅ White", "usr:lst:white")],
        [_btn("⚫️ Black", "usr:lst:black")],
        [_btn("⬅️ Назад", "usr:root")],
    ])


@router.callback_query(F.data == "usr:lists")
async def usr_lists(cq: CallbackQuery, state: FSMContext):
    if not _admin(cq.from_user.id):
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("📋 Списки доступа — выберите:", reply_markup=kb_lists_root())
    except Exception:
        await cq.message.answer("📋 Списки доступа — выберите:", reply_markup=kb_lists_root())
    await cq.answer()


_HINT = {
    "ban": "🚫 <b>BAN</b> — отправка полностью запрещена.",
    "white": "✅ <b>White</b> — обычная отправка.",
    "black": "⚫️ <b>Black</b> — профиль black (рубрика «black»).\n<i>Не в white и не в ban = тоже black.</i>",
}


def _kb_list(lst: str) -> InlineKeyboardMarkup:
    rows = []
    for e in access.list_entries(lst):
        tag = "" if e.is_id else "@"
        rows.append([_btn(f"🗑 {tag}{e.value}", f"usr:del:{e.id}:{lst}")])
    rows.append([_btn("+ Добавить", f"usr:add:{lst}")])
    rows.append([_btn("⬅️ Назад", "usr:lists")])
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
