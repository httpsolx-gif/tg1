"""OTHER → 🔑 Ключи: общие токены Cloudflare / NameSilo / Dynadot (bot/services/keys.py).

Колбэки `key:*`. Ввод значения — сообщением; сообщение с ключом сразу удаляется из чата.
"""
from __future__ import annotations

from html import escape as _esc

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from common.config import settings
from bot.fsm.admin import KeyInput
from bot.services import keys

router = Router(name="keys")

_HINT = {
    "cf": "API Token (scoped: Zone·Zone·Edit, Zone·DNS·Edit, Zone·SSL and Certificates·Edit)",
    "namesilo": "API key (namesilo.com → Account → API Manager)",
    "dynadot": "API key (dynadot.com → Tools → API; при необходимости добавьте IP сервера в whitelist)",
}


def _btn(t: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=t, callback_data=cb)


def _view():
    text = (
        "🔑 <b>Ключи</b> — общие для всех short-доменов\n\n"
        + "\n".join(f"• {name}: {keys.masked(k)}" for k, name in keys.NAMES.items())
        + "\n\n<i>Cloudflare — создание зон. NameSilo / Dynadot — авто-смена NS "
          "(регистратор определяется сам: домен есть в NameSilo → он, иначе Dynadot).</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [_btn(f"{'☁️' if k == 'cf' else '🌐'} {name}", f"key:set:{k}")] for k, name in keys.NAMES.items()
    ] + [[_btn("⬅️ Назад", "adm:other")]])
    return text, kb


@router.callback_query(F.data == "key:root")
async def key_root(cq: CallbackQuery, state: FSMContext):
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


@router.callback_query(F.data.startswith("key:set:"))
async def key_set_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    name = cq.data.split(":")[2]
    if name not in keys.NAMES:
        await cq.answer("Неизвестный ключ", show_alert=True)
        return
    await state.set_state(KeyInput.value)
    await state.update_data(key_name=name)
    await cq.message.answer(
        f"🔑 <b>{keys.NAMES[name]}</b>: {keys.masked(name)}\n\n"
        f"Пришлите {_HINT[name]}.\nЧтобы удалить ключ — отправьте <code>-</code>.",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Отмена", "key:root")]]),
    )
    await cq.answer()


async def _verify(name: str, value: str) -> tuple[bool, str]:
    if name == "cf":
        from bot.services import cloudflare as cf
        return await cf.verify_token(value)
    if name == "namesilo":
        from bot.services import namesilo
        return await namesilo.verify_key(value)
    from bot.services import dynadot
    return await dynadot.verify_key(value)


@router.message(KeyInput.value, F.text)
async def key_set_value(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    name = (await state.get_data()).get("key_name")
    value = (m.text or "").strip()
    try:
        await m.delete()  # не держим ключ в истории чата
    except Exception:
        pass
    if value in ("-", "—"):
        keys.set(name, None)
        note = f"🗑 {keys.NAMES[name]}: ключ удалён."
    else:
        ok, info = await _verify(name, value)
        if not ok:
            await m.answer(f"🔴 {keys.NAMES[name]}: ключ не прошёл проверку — <code>{_esc(str(info)[:200])}</code>\n"
                           "Пришлите другой или «Отмена».",
                           reply_markup=InlineKeyboardMarkup(inline_keyboard=[[_btn("⬅️ Отмена", "key:root")]]))
            return
        keys.set(name, value)
        note = f"✅ {keys.NAMES[name]}: сохранён."
    await state.clear()
    text, kb = _view()
    await m.answer(note + "\n\n" + text, reply_markup=kb)
