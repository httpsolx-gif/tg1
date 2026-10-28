from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Optional

from aiogram import Router, F
from aiogram.filters import Command, StateFilter
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

from sqlmodel import Session, select

from common.db import get_engine
from common.models.send_log import SendLog
from common.models.node import Node
from common.models.aws import AwsAccount
from common.models.smtp import SmtpAccount
from bot.services.smtp_pool import pick_smtp
from bot.services.templates import render_template, default_template, resolve_template_html
from bot.services.short_client import shorten_url
from bot.services import mailer as mailer_svc
from bot.services.node_runner import send_via_node, send_ses_via_node
from bot.services.aws_ses import send_via_ses
from bot.keyboards.admin_menu import ADMIN_BUTTONS
from bot.keyboards.icons import strip_lead_emoji as _strip
from bot.handlers.admin.rubrics import rubric_for_email, rubric_group

# тексты нижнего меню — выход из флоу
_MENU_TEXTS = {"⬅️ Назад", "🛠 Админка", "📋 Меню"} | {t for row in ADMIN_BUTTONS for t in row}
_MENU_TEXTS |= {_strip(t) for t in set(_MENU_TEXTS)}

log = logging.getLogger("bot")
router = Router(name="flow-send")
_engine = get_engine()


class SendFlow(StatesGroup):
    country = State()
    service = State()
    link = State()
    email = State()


# ---------- keyboards ----------
def _kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="send:cancel")]])


def _kb_countries() -> InlineKeyboardMarkup:
    """Страны, у которых есть сервисы (COUNTRY_SERVICES непустой)."""
    from common.constants import COUNTRIES, COUNTRY_FLAGS, COUNTRY_SERVICES
    btns = [
        InlineKeyboardButton(text=f"{COUNTRY_FLAGS.get(c, '')} {c}", callback_data=f"send:ctry:{c}")
        for c in COUNTRIES if COUNTRY_SERVICES.get(c)
    ]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="send:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_services(country: str) -> InlineKeyboardMarkup:
    """Сервисы выбранной страны (из COUNTRY_SERVICES); сервис можно и ввести текстом."""
    from common.constants import COUNTRY_SERVICES
    services = COUNTRY_SERVICES.get(country, [])
    btns = [InlineKeyboardButton(text=f"🔹 {s}", callback_data=f"send:svc:{s}") for s in services]
    rows = [btns[i:i + 2] for i in range(0, len(btns), 2)]
    rows.append([InlineKeyboardButton(text="⬅️ Страны", callback_data="send:ctries")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="send:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _short_enabled(service: Optional[str], country: Optional[str], target: str) -> bool:
    """Сокращать ли ссылку — по привязкам раздела SHORT (сервис > страна > рубрика-target > global)."""
    from bot.services.short_policy import get_binding
    with Session(_engine) as db:
        return get_binding(db, service=service, country=country, target=target) is not None


def _node_for_aws(aws) -> "Node | None":
    """Нода для отправки SES: СНАЧАЛА активная нода с совпадающим регионом
    (гео AWS-аккаунта → нода с тем же region), затем явная привязка aws.node_id."""
    reg = (aws.region or "").strip().lower()
    with Session(_engine) as db:
        nodes = db.exec(
            select(Node).where(Node.is_active == True).order_by(Node.error_count, Node.id)  # noqa: E712
        ).all()
    if reg:
        for n in nodes:
            if (n.region or "").strip().lower() == reg:
                return n
    if aws.node_id:
        for n in nodes:
            if n.id == aws.node_id:
                return n
    return None


def _kb_result() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✉️ Новое письмо", callback_data="send:new")]])


# ---------- entry ----------
@router.message(Command("send"))
@router.message(F.text.func(lambda t: isinstance(t, str) and _strip(t) == "Отправить письмо"))
async def start_send(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(SendFlow.country)
    await m.answer("✉️ Новое письмо.\n\n🌍 Выберите страну:", reply_markup=_kb_countries())


@router.callback_query(F.data == "send:new")
async def send_new(cq: CallbackQuery, state: FSMContext):
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
    txt = f"Страна: <b>{code}</b>\n\n🔹 Выберите сервис или введите вручную (напр. myemail.pl):"
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
        f"Сервис: <b>{service}</b>\n\n🔗 Пришлите ссылку (подставится вместо <code>MAIL_URL</code> в кнопке письма):",
        reply_markup=_kb_cancel(),
    )
    await cq.answer()


@router.message(SendFlow.service, F.text)
async def take_service_text(m: Message, state: FSMContext):
    service = (m.text or "").strip()
    if not service:
        await m.answer("Пустой сервис. Введите ещё раз (напр. myemail.pl):")
        return
    await state.update_data(service=service)
    await state.set_state(SendFlow.link)
    await m.answer(
        f"Сервис: <b>{service}</b>\n\n🔗 Пришлите ссылку (подставится вместо <code>MAIL_URL</code> в кнопке письма):",
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
    rub = rubric_for_email(email)
    if not rub:
        dom = email.rsplit("@", 1)[-1]
        await state.clear()
        await m.answer(f"🚫 Нет рубрики для домена <b>{dom}</b>. Добавь его в «✉️ Почты» нужной рубрики или создай рубрику <b>other</b> — она ловит все остальные домены (🧩 SRVS → Рубрики).")
        return
    data = await state.get_data()
    await state.clear()
    service = data["service"]
    code = (rub["name"][:1] or "").lower()
    # почта по домену + возможное переопределение (сервис×рубрика×почта)
    from bot.services.mailboxes import mailbox_for_email
    from bot.handlers.admin.rubrics import service_rubric_cfg
    mailbox = mailbox_for_email(email, default="main")
    cfg = service_rubric_cfg(service, rub["id"], mailbox) or {}
    sender = cfg.get("sender_name") or rub.get("sender_name") or "—"
    title = cfg.get("title") or rub.get("title") or "—"
    # шаблон: конфиг сервиса → под рубрику (service-код) → общий (service.html) → стандартный
    tpl_spec = resolve_template_html(service, code)
    tpl_gen = resolve_template_html(service, "") if tpl_spec is None else None
    if cfg.get("template_html"):
        tpl_name = "(из конфига сервиса)"
    elif tpl_spec is not None:
        tpl_name = f"<code>{service}-{code}.html</code>"
    elif tpl_gen is not None:
        tpl_name = f"<code>{service}.html</code> (общий)"
    else:
        tpl_name = f"<code>{service}-{code}.html</code> ⚠️ (нет — будет стандартный)"
    ovr = " 🔧" if cfg else ""
    await m.answer(
        f"Рубрика по домену: <b>{rub['name']}</b> · почта <b>{mailbox}</b>{ovr}\n"
        f"• Шаблон: {tpl_name}\n"
        f"• Тайтл: {title}\n"
        f"• Отправитель: {sender}\n\n"
        "⏳ Отправляю…"
    )
    group = rubric_group(rub)
    # SHORT: область (страна×сервис×рубрика, ALL; побеждает настроенная последней) → раздел SHORT
    # Провайдер: 'off' | 'n9.cl' | 'dom:{id}' (личный домен). Нет настройки области → легаси ShortBinding.
    from bot.services.scope import scope_short_choice
    provider = scope_short_choice(data.get("country"), service, rub["id"])
    if provider is None:
        provider = "n9.cl" if _short_enabled(service, data.get("country"), group) else "off"
    html = (
        cfg.get("template_html")
        or tpl_spec
        or tpl_gen
        or default_template()
    )
    payload = {
        "group": group,
        "country": data.get("country"),
        "service": service,
        "sender": cfg.get("sender_name") or rub.get("sender_name"),
        "subject": cfg.get("title") or rub.get("title") or "Notification",
        "template": html,
        "link": data["link"],
        "email": email,
        "short_provider": provider,
        "mailbox": mailbox,
        "rubric_id": rub["id"],
        "rubric_name": f"{rub['name']} · {mailbox}",
    }
    await _do_send(m, payload, user_id=m.from_user.id)


async def _resolve_short(link: str, provider: str, track: Optional[str] = None) -> str:
    """Короткая ссылка по провайдеру области: off → как есть, dom:{id} → личный домен
    (локально, без сети, с трек-id для статистики), иначе → n9."""
    if not link or not provider or provider == "off":
        return link
    if provider.startswith("dom:"):
        from bot.services.short_domain import personal_short
        return await personal_short(provider, link, track)
    return await shorten_url(link)


def _first_node() -> "Node | None":
    """Нода для SMTP: первая активная (по id)."""
    with Session(_engine) as db:
        return db.exec(select(Node).where(Node.is_active == True).order_by(Node.id)).first()  # noqa: E712


async def _do_send(target, payload: dict, *, user_id: Optional[int]) -> None:
    say = target.answer
    provider = payload.get("short_provider") or "off"
    # трек-id только для личных доменов (по нему привязываем переход к отправке)
    track = secrets.token_hex(6) if provider.startswith("dom:") else None
    short = await _resolve_short(payload["link"], provider, track)
    html = render_template(payload["template"], title=payload["subject"], url=short)

    smtp = None
    from_disp = None
    via = "локально"
    # аккаунт — из базы области (SMTP и AWS вместе, автосмена); нет базы → SMTP раздела SMTP
    from bot.services.scope import scope_pick
    has_base, pick = scope_pick(payload.get("country"), payload.get("service"), payload.get("rubric_id"))
    if not has_base:
        smtp = await pick_smtp(
            group=payload["group"], country_code=payload.get("country"), service=payload.get("service")
        )
        pick = ("smtp", smtp) if smtp else None
    if pick is None:
        where = "в базе области (все исчерпаны/выключены)" if has_base else f"(базы области нет, в разделе SMTP для группы «{payload['group']}» тоже)"
        await say(f"🚫 Нет доступного аккаунта для отправки {where}.")
        return
    kind, acc = pick

    if kind == "aws":
        from_disp = acc.email_from
        node = _node_for_aws(acc)  # нода с тем же регионом
        if node is not None:
            via = f"SES через ноду {node.login}@{node.ip} ({acc.region})"
            ok, info = await send_ses_via_node(node, acc, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))
        else:
            via = f"SES {acc.region}, с бота — нет ноды с этим регионом"
            ok, info = await send_via_ses(acc, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))
    else:
        smtp = acc
        from_disp = smtp.email
        node = _first_node()
        if node is not None:
            via = f"SMTP через ноду {node.login}@{node.ip}"
            ok, info = await send_via_node(node, smtp, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))
        else:
            via = "SMTP локально (активных нод нет)"
            ok, info = await mailer_svc.send_via_account(smtp, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))

    if ok:
        try:
            with Session(_engine) as db:
                row = db.get(SmtpAccount if kind == "smtp" else AwsAccount, acc.id)
                if row:
                    row.sent_count = (row.sent_count or 0) + 1
                    db.add(row)
                    db.commit()
        except Exception as e:
            log.warning("sent_count: %s", e)

    try:
        with Session(_engine) as db:
            db.add(SendLog(
                user_id=user_id, smtp_id=getattr(smtp, "id", None), email=payload["email"],
                status="ok" if ok else "fail", error=None if ok else (info or "send failed"),
                created_at=datetime.utcnow().isoformat(),
                country=payload.get("country"), service=payload.get("service"),
                mailbox=payload.get("mailbox"), short_provider=provider, track=track,
            ))
            db.commit()
    except Exception as e:
        log.warning("SendLog: %s", e)

    sender = payload.get("sender")
    frm = f"{sender} &lt;{from_disp}&gt;" if sender else from_disp
    lines = [
        "Отправлено" if ok else "❌ Ошибка при отправке",
        "",
        f"Сервис: {payload.get('service') or '—'}",
        f"Рубрика: {payload.get('rubric_name')}",
        f"Ссылка: {short or '—'}",
        f"Кому: {payload['email']}",
        f"От: {frm}",
    ]
    # ответ провайдера (MessageId и т.п.) показываем только при ошибке
    if not ok:
        lines.append(f"Через: {via}")
        if info:
            lines.append(f"\n<code>{info}</code>")
    await say("\n".join(lines), reply_markup=_kb_result())
