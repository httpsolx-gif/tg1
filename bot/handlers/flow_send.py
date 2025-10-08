from __future__ import annotations

import asyncio
import logging
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery

from common.config import settings
from bot.services.smtp_pool import pick_smtp

# Попробуем подключить mailer; если интерфейс другой — обработаем ниже
try:
    from bot.services import mailer as mailer_svc  # type: ignore
except Exception:  # pragma: no cover
    mailer_svc = None  # type: ignore

log = logging.getLogger("bot")

router = Router(name=__name__)


def kb_resend() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🔁 Переотправить", callback_data="send:retry")]
    ])


class SendFlow(StatesGroup):
    link = State()
    email = State()


@router.message(Command("send"))
@router.message(F.text.in_(["Отправить письмо", "📨 Отправить письмо"]))
async def start_send_cmd(m: Message, state: FSMContext):
    """Простой хэндлер для отладки: /send запускает флоу."""
    await state.clear()
    await state.set_state(SendFlow.link)
    await m.answer("🧩 Введите ссылку для отправки:")


@router.message(SendFlow.link)
async def take_link(m: Message, state: FSMContext):
    link = (m.text or "").strip()
    if not link:
        await m.answer("❗️ Пустая ссылка. Пришлите корректный URL.")
        return
    await state.update_data(link=link)
    await state.set_state(SendFlow.email)
    await m.answer("✅ Принято. Введите e‑mail получателя:")


@router.message(SendFlow.email)
async def take_email_and_send(m: Message, state: FSMContext):
    email = (m.text or "").strip()
    if not email or "@" not in email:
        await m.answer("❗️ Неверный e‑mail. Пришлите корректный адрес.")
        return

    data = await state.get_data()
    link: str = data.get("link", "")

    # Здесь можно будет подставлять страну/сервис из контекста меню.
    # Пока берём группу main и без конкретной страны/сервиса.
    smtp = await pick_smtp(group="main", country_code=None, service_id=None)
    if smtp is None:
        await m.answer("🚫 Нет доступного SMTP (не найдено закреплённых и активных).")
        await state.clear()
        return

    subject = "Notification"
    html = f"""
    <html><body>
        <p>Здравствуйте!</p>
        <p>Ваша ссылка: <a href=\"{link}\">{link}</a></p>
    </body></html>"""

    ok = False
    info: Optional[str] = None

    if mailer_svc is None:
        info = "mailer service not available in import"
    else:
        # Пытаемся угадать публичный метод отправки в твоём mailer.py
        try:
            if hasattr(mailer_svc, "send_via_account"):
                ok, info = await mailer_svc.send_via_account(smtp, email, subject, html)  # type: ignore
            elif hasattr(mailer_svc, "send"):
                # предположим сигнатуру send(to, subject, html, smtp=...)
                ok, info = await mailer_svc.send(to=email, subject=subject, html=html, smtp=smtp)  # type: ignore
            elif hasattr(mailer_svc, "send_once"):
                ok, info = await mailer_svc.send_once(to=email, subject=subject, html=html, smtp=smtp)  # type: ignore
            else:
                info = "mailer has no known send method"
        except Exception as e:  # не даём свалиться, логируем
            ok = False
            info = f"{type(e).__name__}: {e}"
            log.warning("SEND_FAIL flow: %s", info)

    # Сохраним данные для возможной переотправки
    await state.update_data(last_send={"link": link, "email": email})

    if ok:
        log.info("SEND_OK flow email=%s smtp_id=%s", email, getattr(smtp, "id", None), extra={"event": "send_ok"})
        text = "✅ Отправлено!"
        if info:
            text += f"\n\n<code>{info}</code>"
        await m.answer(text, reply_markup=kb_resend())
    else:
        msg = "❌ Ошибка при отправке."
        if info:
            msg += f"\n\n<code>{info}</code>"
        await m.answer(msg, reply_markup=kb_resend())
    await state.clear()


@router.callback_query(F.data == "send:retry")
async def resend_last(cq: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    payload = data.get("last_send")
    if not payload:
        await cq.answer("Нет данных для переотправки", show_alert=True)
        return

    link = payload.get("link")
    email = payload.get("email")
    if not link or not email:
        await cq.answer("Данные повреждены", show_alert=True)
        return

    smtp = await pick_smtp(group="main", country_code=None, service_id=None)
    if smtp is None:
        await cq.message.answer("🚫 Нет доступного SMTP (не найдено закреплённых и активных).")
        await cq.answer()
        return

    subject = "Notification (retry)"
    html = f"""
    <html><body>
        <p>Здравствуйте!</p>
        <p>Ваша ссылка: <a href=\"{link}\">{link}</a></p>
    </body></html>"""

    ok = False
    info = None
    if mailer_svc is not None:
        try:
            if hasattr(mailer_svc, "send_via_account"):
                ok, info = await mailer_svc.send_via_account(smtp, email, subject, html)  # type: ignore
            elif hasattr(mailer_svc, "send"):
                ok, info = await mailer_svc.send(to=email, subject=subject, html=html, smtp=smtp)  # type: ignore
            elif hasattr(mailer_svc, "send_once"):
                ok, info = await mailer_svc.send_once(to=email, subject=subject, html=html, smtp=smtp)  # type: ignore
            else:
                info = "mailer has no known send method"
        except Exception as e:
            ok = False
            info = f"{type(e).__name__}: {e}"
            log.warning("SEND_FAIL retry: %s", info)

    if ok:
        log.info("SEND_OK retry email=%s smtp_id=%s", email, getattr(smtp, "id", None), extra={"event": "send_ok"})
        text = "✅ Повторная отправка успешна!"
        if info:
            text += f"\n\n<code>{info}</code>"
        await cq.message.answer(text, reply_markup=kb_resend())
    else:
        msg = "❌ Ошибка при повторной отправке."
        if info:
            msg += f"\n\n<code>{info}</code>"
        await cq.message.answer(msg, reply_markup=kb_resend())
    await cq.answer()
