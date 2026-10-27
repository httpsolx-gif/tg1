from __future__ import annotations

import logging
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
from common.models.template import EmailTemplate
from common.models.smtp import SmtpAccount
from bot.services.smtp_pool import pick_smtp
from bot.services.templates import render_template, default_template, resolve_template_html
from bot.services.short_client import shorten_url
from bot.services import mailer as mailer_svc
from bot.services.node_runner import send_via_node, send_ses_via_node
from bot.services.aws_ses import send_via_ses
from bot.keyboards.admin_menu import ADMIN_BUTTONS
from bot.keyboards.icons import strip_lead_emoji as _strip
from bot.handlers.admin.rubrics import rubric_for_email

# тексты нижнего меню — выход из флоу
_MENU_TEXTS = {"⬅️ Назад", "🛠 Админка", "📋 Меню"} | {t for row in ADMIN_BUTTONS for t in row}
_MENU_TEXTS |= {_strip(t) for t in set(_MENU_TEXTS)}

log = logging.getLogger("bot")
router = Router(name="flow-send")
_engine = get_engine()


class SendFlow(StatesGroup):
    service = State()
    link = State()
    email = State()
    backend = State()


# ---------- keyboards ----------
def _kb_cancel() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✖️ Отмена", callback_data="send:cancel")]])


def _kb_services():
    with Session(_engine) as db:
        rows_ = db.exec(select(EmailTemplate.service).distinct().order_by(EmailTemplate.service)).all()
    services = [r if isinstance(r, str) else r[0] for r in rows_]
    rows = [[InlineKeyboardButton(text=f"🔹 {s}", callback_data=f"send:svc:{s}")] for s in services]
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="send:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows), services


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


def _kb_backends() -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text="🖥 Локально", callback_data="send:be:local")]]
    with Session(_engine) as db:
        nodes = db.exec(select(Node).where(Node.is_active == True).order_by(Node.id)).all()  # noqa: E712
        aws = db.exec(select(AwsAccount).where(AwsAccount.is_active == True).order_by(AwsAccount.id)).all()  # noqa: E712
    for n in nodes:
        geo = f" · {n.region}" if n.region else ""
        rows.append([InlineKeyboardButton(text=f"🖥 {n.login}@{n.ip}{geo}", callback_data=f"send:be:node:{n.id}")])
    for a in aws:
        node = _node_for_aws(a)
        tag = f" ({a.region}→нода)" if node else f" ({a.region}→bot)"
        rows.append([InlineKeyboardButton(text=f"☁️ SES {a.email_from}{tag}", callback_data=f"send:be:aws:{a.id}")])
    rows.append([InlineKeyboardButton(text="✖️ Отмена", callback_data="send:cancel")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_result() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✉️ Новое письмо", callback_data="send:new")]])


# ---------- entry ----------
@router.message(Command("send"))
@router.message(F.text.in_(["📤 Отправить письмо", "Отправить письмо", "📨 Отправить письмо"]))
async def start_send(m: Message, state: FSMContext):
    await state.clear()
    await state.set_state(SendFlow.service)
    kb, services = _kb_services()
    hint = "Выберите сервис или введите вручную (напр. olx.pl):" if services else "Введите сервис (напр. olx.pl):"
    await m.answer(f"✉️ Новое письмо.\n\n🔹 {hint}", reply_markup=kb)


@router.callback_query(F.data == "send:new")
async def send_new(cq: CallbackQuery, state: FSMContext):
    await state.clear()
    await state.set_state(SendFlow.service)
    kb, _ = _kb_services()
    await cq.message.answer("✉️ Новое письмо.\n\n🔹 Выберите сервис или введите вручную:", reply_markup=kb)
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
    StateFilter(SendFlow.service, SendFlow.link, SendFlow.email, SendFlow.backend),
    F.text.in_(_MENU_TEXTS),
)
async def send_cancel_on_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Отправка отменена.")


# ---------- service ----------
@router.callback_query(SendFlow.service, F.data.startswith("send:svc:"))
async def take_service_cb(cq: CallbackQuery, state: FSMContext):
    service = cq.data.split(":", 2)[2]
    await state.update_data(service=service)
    await state.set_state(SendFlow.link)
    await cq.message.edit_text(f"Сервис: <b>{service}</b>\n\n🔗 Введите ссылку для письма:", reply_markup=_kb_cancel())
    await cq.answer()


@router.message(SendFlow.service, F.text)
async def take_service_text(m: Message, state: FSMContext):
    service = (m.text or "").strip()
    if not service:
        await m.answer("Пустой сервис. Введите ещё раз (напр. olx.pl):")
        return
    await state.update_data(service=service)
    await state.set_state(SendFlow.link)
    await m.answer(f"Сервис: <b>{service}</b>\n\n🔗 Введите ссылку для письма:", reply_markup=_kb_cancel())


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
        await m.answer(f"🚫 Нет рубрики для домена <b>{dom}</b>. Создай рубрику с этим доменом в 🧩 SRVS.")
        return
    data = await state.get_data()
    await state.update_data(email=email, rubric=rub)
    await state.set_state(SendFlow.backend)
    service = data["service"]
    code = (rub["name"][:1] or "").lower()
    # почта по домену + возможное переопределение (сервис×рубрика×почта)
    from bot.services.mailboxes import mailbox_for_email
    from bot.handlers.admin.rubrics import service_rubric_cfg
    mailbox = mailbox_for_email(email, default="main")
    cfg = service_rubric_cfg(service, rub["id"], mailbox) or {}
    sender = cfg.get("sender_name") or rub.get("sender_name") or "—"
    title = cfg.get("title") or rub.get("title") or "—"
    has_tpl = bool(cfg.get("template_html")) or resolve_template_html(service, code) is not None
    tpl_name = "(из конфига сервиса)" if cfg.get("template_html") else f"<code>{service}-{code}.html</code>"
    tpl_note = "" if has_tpl else " ⚠️ (нет — будет стандартный)"
    ovr = " 🔧" if cfg else ""
    await m.answer(
        f"Рубрика по домену: <b>{rub['name']}</b> · почта <b>{mailbox}</b>{ovr}\n"
        f"• SMTP-группа: {rub.get('smtp_group') or 'main'}\n"
        f"• Шаблон: {tpl_name}{tpl_note}\n"
        f"• Тайтл: {title}\n"
        f"• Отправитель: {sender}\n\n"
        "🖥 Откуда отправлять?",
        reply_markup=_kb_backends(),
    )


# ---------- backend → отправка ----------
@router.callback_query(SendFlow.backend, F.data.startswith("send:be:"))
async def take_backend(cq: CallbackQuery, state: FSMContext):
    parts = cq.data.split(":")  # send:be:<kind>[:id]
    kind = parts[2]
    node_id = aws_id = None
    if kind == "node":
        node_id = int(parts[3])
    elif kind == "aws":
        aws_id = int(parts[3])
    data = await state.get_data()
    await state.clear()
    rub = data["rubric"]
    service = data["service"]
    email = data["email"]
    # почта — по домену получателя; конфиг (сервис×рубрика×почта) переопределяет
    from bot.services.mailboxes import mailbox_for_email
    from bot.handlers.admin.rubrics import service_rubric_cfg
    mailbox = mailbox_for_email(email, default="main")
    cfg = service_rubric_cfg(service, rub["id"], mailbox) or {}
    code = (rub["name"][:1] or "").lower()
    html = cfg.get("template_html") or resolve_template_html(service, code) or default_template()
    payload = {
        "group": rub.get("smtp_group") or "main",
        "sender": cfg.get("sender_name") or rub.get("sender_name"),
        "subject": cfg.get("title") or rub.get("title") or "Notification",
        "template": html,
        "link": data["link"],
        "email": email,
        "shorten": bool(rub.get("shortener")),
        "node_id": node_id,
        "aws_id": aws_id,
        "rubric_name": f"{rub['name']} · {mailbox}",
    }
    try:
        await cq.message.edit_text("⏳ Отправляю…")
    except Exception:
        pass
    await cq.answer()
    await _do_send(cq.message, payload, user_id=cq.from_user.id)


async def _do_send(target, payload: dict, *, user_id: Optional[int]) -> None:
    say = target.answer
    short = await shorten_url(payload["link"]) if (payload["link"] and payload.get("shorten")) else payload["link"]
    html = render_template(payload["template"], title=payload["subject"], url=short)

    aws_id = payload.get("aws_id")
    node_id = payload.get("node_id")
    smtp = None
    from_disp = None
    via = "локально"

    if aws_id is not None:
        with Session(_engine) as db:
            aws = db.get(AwsAccount, aws_id)
        if aws is None:
            await say("🚫 AWS-аккаунт не найден.")
            return
        from_disp = aws.email_from
        node = _node_for_aws(aws)  # маршрут по региону (гео), затем ручная привязка
        if node is not None:
            via = f"SES через ноду {node.login}@{node.ip} ({aws.region})"
            ok, info = await send_ses_via_node(node, aws, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))
        else:
            via = f"SES {aws.email_from} ({aws.region}, бот-side — нет ноды с этим гео)"
            ok, info = await send_via_ses(aws, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))
    else:
        smtp = await pick_smtp(group=payload["group"])
        if smtp is None:
            await say(f"🚫 Нет доступного SMTP для группы «{payload['group']}».")
            return
        from_disp = smtp.email
        if node_id is not None:
            with Session(_engine) as db:
                node = db.get(Node, node_id)
            if node is None:
                await say("🚫 Нода не найдена.")
                return
            via = f"нода {node.login}@{node.ip}"
            ok, info = await send_via_node(node, smtp, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))
        else:
            ok, info = await mailer_svc.send_via_account(smtp, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))

    if smtp is not None and ok:
        try:
            with Session(_engine) as db:
                acc = db.get(SmtpAccount, smtp.id)
                if acc:
                    acc.sent_count = (acc.sent_count or 0) + 1
                    db.add(acc)
                    db.commit()
        except Exception as e:
            log.warning("sent_count: %s", e)

    try:
        with Session(_engine) as db:
            db.add(SendLog(
                user_id=user_id, smtp_id=getattr(smtp, "id", None), email=payload["email"],
                status="ok" if ok else "fail", error=None if ok else (info or "send failed"),
                created_at=datetime.utcnow().isoformat(),
            ))
            db.commit()
    except Exception as e:
        log.warning("SendLog: %s", e)

    head = "✅ Отправлено!" if ok else "❌ Ошибка при отправке."
    lines = [
        head,
        f"• Рубрика: <b>{payload.get('rubric_name')}</b>",
        f"• Кому: <code>{payload['email']}</code>",
        f"• Откуда: {via}",
        f"• От: <b>{payload.get('sender') or from_disp}</b> &lt;{from_disp}&gt;",
        f"• Тема: {payload['subject']}",
        f"• Ссылка: {short or '—'}",
    ]
    if info:
        lines.append(f"\n<code>{info}</code>")
    await say("\n".join(lines), reply_markup=_kb_result())
