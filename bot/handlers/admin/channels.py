"""OTHER → 📣 Каналы: куда бот пишет автосмены / ошибки и атаки / отправки / спам / бэкапы (п.118).

Колбэки `chan:*`. Канал задаётся пересылкой любого поста из него или id (`-100…`/`@name`);
перед сохранением бот шлёт пробное сообщение — не может писать (не админ) → не сохраняем.
"""
from __future__ import annotations

from html import escape as _esc

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from common import channels
from common.config import settings
from common.tgids import norm_chat_id
from bot.fsm.admin import ChannelInput

router = Router(name="channels")


def _btn(t: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t, callback_data=cb)


def _state(kind: str) -> str:
    cid, src = channels.get(kind), channels.source(kind)
    if not cid:
        return "⚪️ не задан"
    return f"🟢 <code>{_esc(cid)}</code>" + (" (.env)" if src == "env" else "")


def _view():
    lines = [f"{label}: {_state(k)}\n   <i>{_esc(what)}</i>" for k, (label, _env, what) in channels.KINDS.items()]
    text = ("📣 <b>Каналы</b> — бот должен быть админом каждого канала\n\n" + "\n".join(lines))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{label} — {'🟢' if channels.get(k) else '⚪️'}", f"chan:set:{k}")]
        for k, (label, _e, _w) in channels.KINDS.items()
    ] + [[_btn("⬅️ Назад", "adm:other")]])
    return text, kb


@router.callback_query(F.data == "chan:root")
async def chan_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    text, kb = _view()
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("chan:set:"))
async def chan_set_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    kind = cq.data.split(":")[2]
    if kind not in channels.KINDS:
        await cq.answer("Неизвестный канал", show_alert=True)
        return
    label, env, what = channels.KINDS[kind]
    await state.set_state(ChannelInput.value)
    await state.update_data(chan_kind=kind)
    await cq.message.answer(
        f"{label}: {_state(kind)}\n<i>{_esc(what)}</i>\n\n"
        "Перешлите сюда <b>любой пост из канала</b> или пришлите его id (<code>-100…</code> / <code>@name</code>).\n"
        "Сначала добавьте бота в канал админом.\n"
        "<code>-</code> — выключить" + (f", <code>env</code> — брать из .env ({env})" if env else "") + ".",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Отмена", "chan:root")]]),
    )
    await cq.answer()


@router.message(ChannelInput.value)
async def chan_set_value(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    kind = (await state.get_data()).get("chan_kind")
    if kind not in channels.KINDS:
        await state.clear()
        return
    label = channels.KINDS[kind][0]
    fwd = getattr(m, "forward_from_chat", None) or getattr(getattr(m, "forward_origin", None), "chat", None)
    raw = str(fwd.id) if fwd else (m.text or "").strip()
    if raw.lower() == "env":
        channels.set(kind, None)
        note = f"↩️ {label}: берётся из .env"
    elif raw in ("-", "—"):
        channels.set(kind, "-")
        note = f"⚪️ {label}: выключен"
    else:
        cid = norm_chat_id(raw)
        if not cid or not (cid.startswith("@") or cid.lstrip("-").isdigit()):
            await m.answer("Не похоже на канал. Перешлите пост из канала или пришлите <code>-100…</code>.")
            return
        try:
            await m.bot.send_message(cid, f"✅ Канал подключён: {label}")
        except Exception as e:
            await m.answer(f"🔴 Не могу писать в <code>{_esc(cid)}</code>: <code>{_esc(str(e)[:200])}</code>\n"
                           "Добавьте бота в канал админом и пришлите ещё раз.",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Отмена", "chan:root")]]))
            return
        channels.set(kind, cid)
        note = f"✅ {label}: <code>{_esc(cid)}</code> (пробное сообщение отправлено)"
    await state.clear()
    text, kb = _view()
    await m.answer(note + "\n\n" + text, reply_markup=kb)
