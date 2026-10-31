from __future__ import annotations

import logging
from html import escape as _esc
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from bot.keyboards.admin_menu import ADMIN_BUTTONS
from bot.keyboards.icons import strip_lead_emoji as _strip

# Ядро отправки (сборка payload + сам сенд + лог) вынесено в bot/services/send_core —
# им пользуются и этот флоу, и API-воркер. Здесь остаётся только Telegram-UX.

# тексты нижнего меню — выход из флоу
_MENU_TEXTS = {"⬅️ Назад", "🛠 Админка", "📋 Меню", "📨 Отправить письмо"} | {t for row in ADMIN_BUTTONS for t in row}
_MENU_TEXTS |= {_strip(t) for t in set(_MENU_TEXTS)}

log = logging.getLogger("bot")
router = Router(name="flow-send")


def _sender_class(user) -> str:
    """'ban' | 'white' | 'black' по id/username отправителя (админ → white)."""
    from bot.services.access import classify
    from common.config import settings as _s
    return classify(user.id, getattr(user, "username", None), _s.admin_ids_set)


class SendFlow(StatesGroup):
    country = State()
    service = State()
    link = State()
    email = State()


# ---------- keyboards ----------
def _kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="send:cancel")]])


def _kb_countries() -> InlineKeyboardMarkup:
    """Страны каталога, у которых есть сервисы (пустые скрыты). Каталог — БД (п.61)."""
    from common import catalog
    have = catalog.country_services(only_with_services=True)
    btns = [
        InlineKeyboardButton(text=f"{c.flag} {c.code}", callback_data=f"send:ctry:{c.code}")
        for c in catalog.countries() if c.code in have
    ]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="send:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_services(country: str) -> InlineKeyboardMarkup:
    """Сервисы выбранной страны (каталог в БД)."""
    from common import catalog
    services = catalog.services(country)
    btns = [InlineKeyboardButton(text=f"🔹 {s}", callback_data=f"send:svc:{s}") for s in services]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton(text="⬅️ Страны", callback_data="send:ctries")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="send:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# последняя отправка на юзера — для «Переотправить» (пересобирается заново: учтёт SMTP/сокру/шаблон)
_LAST: dict[int, dict] = {}


def _kb_result(resend: bool = False) -> InlineKeyboardMarkup:
    rows = []
    if resend:
        rows.append([InlineKeyboardButton(text="🔁 Переотправить", callback_data="send:resend")])
    rows.append([InlineKeyboardButton(text="✉️ Новое письмо", callback_data="send:new")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- entry ----------
@router.message(Command("send"))
@router.message(F.text.func(lambda t: isinstance(t, str) and _strip(t) == "Отправить письмо"))
async def start_send(m: Message, state: FSMContext):
    from bot.services.access import remember_user
    remember_user(m.from_user.id, m.from_user.username)
    if _sender_class(m.from_user) == "ban":
        await m.answer("🚫 Доступ к отправке заблокирован.")
        return
    await state.clear()
    await state.set_state(SendFlow.country)
    await m.answer("✉️ Новое письмо.\n\n🌍 Выберите страну:", reply_markup=_kb_countries())


@router.callback_query(F.data == "send:resend")
async def send_resend(cq: CallbackQuery):
    from bot.services.access import remember_user
    remember_user(cq.from_user.id, cq.from_user.username)
    if _sender_class(cq.from_user) == "ban":
        await cq.answer("🚫 Доступ к отправке заблокирован.", show_alert=True)
        return
    last = _LAST.get(cq.from_user.id)
    if not last:
        await cq.answer("Нет данных прошлой отправки", show_alert=True)
        return
    await cq.answer("Переотправляю…")
    # пересобираем payload заново — подхватит текущие рубрику/шаблон/сокру/аккаунт
    from bot.services.send_core import build_payload, SendError
    try:
        payload = build_payload(country=last["country"], service=last["service"],
                                email=last["email"], link=last["link"],
                                sender_class=last["sender_class"])
    except SendError as e:
        await cq.message.answer(str(e))
        return
    await cq.message.answer("⏳ Переотправляю…")
    await _do_send(cq.message, payload, user_id=cq.from_user.id)


@router.callback_query(F.data == "send:new")
async def send_new(cq: CallbackQuery, state: FSMContext):
    from bot.services.access import remember_user
    remember_user(cq.from_user.id, cq.from_user.username)
    if _sender_class(cq.from_user) == "ban":
        await cq.answer("🚫 Доступ к отправке заблокирован.", show_alert=True)
        return
    await state.clear()
    await state.set_state(SendFlow.country)
    await cq.message.answer("✉️ Новое письмо.\n\n🌍 Выберите страну:", reply_markup=_kb_countries())
    await cq.answer()


@router.callback_query(F.data == "send:ctries")
async def send_back_countries(cq: CallbackQuery, state: FSMContext):
    await state.set_state(SendFlow.country)
    try:
        await cq.message.edit_text("✉️ Новое письмо.\n\n🌍 Выберите страну:", reply_markup=_kb_countries())
    except Exception:
        await cq.message.answer("✉️ Новое письмо.\n\n🌍 Выберите страну:", reply_markup=_kb_countries())
    await cq.answer()


@router.callback_query(F.data == "send:cancel")
async def send_cancel(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    try:
        await cq.message.edit_text("Отправка отменена.")
    except Exception:
        await cq.message.answer("Отправка отменена.")
    await cq.answer("Отменено")


@router.message(
    StateFilter(SendFlow.country, SendFlow.service, SendFlow.link, SendFlow.email),
    F.text.func(lambda t: isinstance(t, str) and _strip(t) in _MENU_TEXTS),
)
async def send_cancel_on_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отправка отменена.")


# ---------- country ----------
@router.callback_query(SendFlow.country, F.data.startswith("send:ctry:"))
async def take_country(cq: CallbackQuery, state: FSMContext):
    code = cq.data.split(":", 2)[2]
    await state.update_data(country=code)
    await state.set_state(SendFlow.service)
    txt = f"Страна: <b>{code}</b>\n\n🔹 Выберите сервис:"
    try:
        await cq.message.edit_text(txt, reply_markup=_kb_services(code))
    except Exception:
        await cq.message.answer(txt, reply_markup=_kb_services(code))
    await cq.answer()


# ---------- service ----------
@router.callback_query(SendFlow.service, F.data.startswith("send:svc:"))
async def take_service_cb(cq: CallbackQuery, state: FSMContext):
    service = cq.data.split(":", 2)[2]
    await state.update_data(service=service)
    await state.set_state(SendFlow.link)
    await cq.message.edit_text(
        f"Сервис: <b>{_esc(service)}</b>\n\n🔗 Пришлите ссылку (подставится вместо <code>MAIL_URL</code> в кнопке письма):",
        reply_markup=_kb_cancel(),
    )
    await cq.answer()


@router.message(SendFlow.service, F.text)
async def take_service_text(m: Message, state: FSMContext):
    service = (m.text or "").strip()
    data = await state.get_data()
    from common import catalog
    svcs = {x.lower(): x for x in catalog.services(data.get("country") or "")}
    if service.lower() not in svcs:
        return  # только существующий сервис кнопкой/текстом; прочее (в т.ч. email) игнорируем
    service = svcs[service.lower()]
    await state.update_data(service=service)
    await state.set_state(SendFlow.link)
    await m.answer(
        f"Сервис: <b>{_esc(service)}</b>\n\n🔗 Пришлите ссылку (подставится вместо <code>MAIL_URL</code> в кнопке письма):",
        reply_markup=_kb_cancel(),
    )


# ---------- link ----------
@router.message(SendFlow.link, F.text)
async def take_link(m: Message, state: FSMContext):
    link = (m.text or "").strip()
    if not link or "." not in link:
        await m.answer("❗️ Пришлите корректную ссылку.", reply_markup=_kb_cancel())
        return
    await state.update_data(link=link)
    await state.set_state(SendFlow.email)
    await m.answer("📧 Введите e-mail получателя:", reply_markup=_kb_cancel())


# ---------- email → авто-рубрика по домену ----------
@router.message(SendFlow.email, F.text)
async def take_email(m: Message, state: FSMContext):
    email = (m.text or "").strip()
    if not email or "@" not in email:
        await m.answer("❗️ Неверный e-mail.", reply_markup=_kb_cancel())
        return
    # классификация отправителя: ban → блок; black → рубрика «black»; white → обычный путь
    from bot.services.access import remember_user
    remember_user(m.from_user.id, m.from_user.username)
    cls = _sender_class(m.from_user)
    if cls == "ban":
        await state.clear()
        await m.answer("🚫 Доступ к отправке заблокирован.")
        return
    data = await state.get_data()
    await state.clear()
    # сборка payload — общее ядро (bot/services/send_core); ошибки сборки показываем как есть
    from bot.services.send_core import build_payload, SendError
    try:
        payload = build_payload(
            country=data.get("country"), service=data["service"],
            email=email, link=data["link"], sender_class=cls,
        )
    except SendError as e:
        await m.answer(str(e))
        return
    pv = payload["_preview"]
    rub_label = "Рубрика (black-отправитель)" if cls == "black" else "Рубрика по домену"
    fix_line = ""
    if pv.get("email_fixed"):
        o, f = pv["email_fixed"]
        fix_line = f"✏️ Адрес исправлен: <code>{_esc(o)}</code> → <b>{_esc(f)}</b>\n"
    await m.answer(
        f"{fix_line}"
        f"{rub_label}: <b>{_esc(str(pv['rub_name']))}</b> · почта <b>{_esc(str(payload['mailbox']))}</b>{pv['ovr']}\n"
        f"• Шаблон: {pv['tpl_label']}\n"
        f"• Тайтл: {_esc(str(pv['title_disp']))}\n"
        f"• Отправитель: {_esc(str(pv['sender_disp']))}\n\n"
        "⏳ Отправляю…"
    )
    _LAST[m.from_user.id] = {"country": data.get("country"), "service": data["service"],
                             "email": email, "link": data["link"], "sender_class": cls}
    await _do_send(m, payload, user_id=m.from_user.id)


async def _do_send(target, payload: dict, *, user_id: Optional[int]) -> None:
    """Тонкая обёртка над send_core.send_payload: выполняет отправку и рисует результат."""
    from bot.services.send_core import send_payload
    res = await send_payload(payload, user_id=user_id)
    if res.get("no_account"):
        await target.answer(f"🚫 Нет доступного аккаунта для отправки {_esc(res.get('info', ''))}.")
        return
    ok, info, short, from_disp, via = (
        res["ok"], res["info"], res["short"], res["from_disp"], res["via"])
    sender = payload.get("sender")
    # всё динамическое экранируем: parse_mode=HTML, а ответы SMTP вида «550 <a@b.c>: …»
    # иначе ломают сообщение — юзер не видит ни результата, ни кнопок
    frm = f"{_esc(sender)} &lt;{_esc(from_disp or '')}&gt;" if sender else _esc(from_disp or "—")
    rub = str(payload.get("rubric_name") or "").split(" · ")[0] or "—"  # без «· mailbox»
    lines = [
        "Отправлено ✅" if ok else "❌ Ошибка при отправке",
        "",
        f"Рубрика: {_esc(rub)}",
        f"Ссылка: {_esc(short or '—')}",
        f"Кому: {_esc(payload['email'])}",
        f"От: {frm}",
    ]
    # ответ провайдера (MessageId и т.п.) показываем только при ошибке
    if not ok:
        lines.append(f"Через: {_esc(via or '—')}")
        if info:
            lines.append(f"\n<code>{_esc(str(info)[:800])}</code>")
    await target.answer("\n".join(lines), reply_markup=_kb_result(resend=user_id in _LAST))
