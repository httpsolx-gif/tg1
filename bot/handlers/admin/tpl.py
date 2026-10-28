import logging
import re
from datetime import datetime
from typing import List, Tuple

from aiogram import Router, F
from aiogram.filters import StateFilter
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select

from common.db import get_engine
from common.models.template import EmailTemplate
from common.config import settings
from bot.fsm.admin import TemplateUpload
from bot.services import mailboxes as mb

router = Router(name="templates-admin")
log = logging.getLogger("bot")
_engine = get_engine()

# <service>-<код>.html  или  <service>.html  (service может содержать точки: olx.pl)
_NAME_RE = re.compile(r"^(?P<service>.+)-(?P<code>[A-Za-z0-9]+)\.(?:html?|txt)$", re.IGNORECASE)
_NAME_RE_PLAIN = re.compile(r"^(?P<service>.+)\.(?:html?|txt)$", re.IGNORECASE)


# ---------- keyboards ----------
def kb_templates_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📤 Загрузить", callback_data="tpl:upload"),
            InlineKeyboardButton(text="📋 Список", callback_data="tpl:list"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="tpl:back")],
    ])


def _kb_upload() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Готово", callback_data="tpl:root")],
    ])


def _kb_list() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    with Session(_engine) as db:
        items = db.exec(select(EmailTemplate).order_by(EmailTemplate.service, EmailTemplate.mailbox)).all()
    if not items:
        rows.append([InlineKeyboardButton(text="Пусто — загрузите шаблоны", callback_data="tpl:upload")])
    else:
        for t in items:
            rows.append([InlineKeyboardButton(
                text=f"🗑 {t.service} · {t.mailbox or 'все'}",
                callback_data=f"tpl:del:{t.id}",
            )])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="tpl:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- entry ----------
async def show_templates_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    codes = ", ".join(f"{c}={n}" for c, n in mb.code_to_name().items())
    await m.answer(
        "📑 TEMPLATES — шаблоны писем.\n\n"
        "Загрузка: просто кидайте <b>.html</b> файлы.\n"
        "• <code>service.html</code> → общий шаблон сервиса (на все рубрики)\n"
        "• <code>service-код.html</code> → только под эту почту/рубрику\n"
        f"Коды: {codes}\n"
        "Примеры: <code>myemail.pl.html</code> (все), <code>myemail.pl-g.html</code> (gmail).",
        reply_markup=kb_templates_root(),
    )


@router.callback_query(F.data == "tpl:root")
async def tpl_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("📑 TEMPLATES — шаблоны писем:", reply_markup=kb_templates_root())
    except Exception:
        await cq.message.answer("📑 TEMPLATES — шаблоны писем:", reply_markup=kb_templates_root())
    await cq.answer()


@router.callback_query(F.data == "tpl:back")
async def tpl_back(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    from bot.handlers.admin.index import back_to_admin
    await back_to_admin(cq)


# ---------- upload ----------
@router.callback_query(F.data == "tpl:upload")
async def tpl_upload(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    codes = ", ".join(f"{c}={n}" for c, n in mb.code_to_name().items())
    await cq.message.answer(
        "Кидайте <b>.html</b> файлы (можно по одному, подряд несколько).\n\n"
        "• <code>service.html</code> — общий (на все рубрики)\n"
        "• <code>service-код.html</code> — под конкретную почту/рубрику\n"
        f"Коды почт: {codes}\n"
        "Напр.: <code>myemail.pl.html</code>, <code>myemail.pl-m.html</code>, <code>myemail.pl-g.html</code>.",
        reply_markup=_kb_upload(),
    )
    await state.set_state(TemplateUpload.waiting)
    await cq.answer()


@router.message(TemplateUpload.waiting, F.document)
async def tpl_upload_doc(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    doc: Document = m.document
    try:
        service, mailbox = parse_template_filename(doc.file_name or "")
    except ValueError as e:
        await m.answer(f"❌ <code>{doc.file_name}</code>: {e}", reply_markup=_kb_upload())
        return
    file = await m.bot.get_file(doc.file_id)
    content = await m.bot.download_file(file.file_path)
    html = content.read().decode("utf-8", errors="ignore")
    created = _upsert_template(service, mailbox, html, doc.file_name)
    verb = "добавлен" if created else "обновлён"
    await m.answer(f"✅ <b>{service}</b> · {mailbox or 'все'} — шаблон {verb}.", reply_markup=_kb_upload())


@router.message(TemplateUpload.waiting, F.text)
async def tpl_upload_hint(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("Пришлите <b>.html</b> файл, либо нажмите «Готово».", reply_markup=_kb_upload())


# Загрузка шаблона «просто кидком» — без кнопок и без входа в режим.
# Срабатывает, когда нет активного FSM-состояния (чтобы не перехватывать .txt
# для SMTP/нод/AWS, которые ждут файл в своих состояниях).
@router.message(StateFilter(None), F.document)
async def tpl_drop_any(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    doc: Document = m.document
    name = (doc.file_name or "")
    low = name.lower()
    if not (low.endswith(".html") or low.endswith(".htm") or low.endswith(".txt")):
        return  # не похоже на шаблон — молча игнорируем
    try:
        service, mailbox = parse_template_filename(name)
    except ValueError as e:
        await m.answer(
            f"📄 <code>{name}</code> — имя не распознано: {e}\n"
            "Формат: <code>service-код.html</code> (напр. <code>myemail.pl-g.html</code>)."
        )
        return
    file = await m.bot.get_file(doc.file_id)
    content = await m.bot.download_file(file.file_path)
    html = content.read().decode("utf-8", errors="ignore")
    created = _upsert_template(service, mailbox, html, name)
    verb = "добавлен" if created else "обновлён"
    await m.answer(f"✅ <b>{service}</b> · {mailbox or 'все'} — шаблон {verb}.")


# ---------- list / delete ----------
@router.callback_query(F.data == "tpl:list")
async def tpl_list(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        await cq.message.edit_text("📑 TEMPLATES → список (нажмите для удаления):", reply_markup=_kb_list())
    except Exception:
        await cq.message.answer("📑 TEMPLATES → список (нажмите для удаления):", reply_markup=_kb_list())
    await cq.answer()


@router.callback_query(F.data.startswith("tpl:del:"))
async def tpl_delete(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        tid = int(cq.data.split(":")[-1])
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return
    with Session(_engine) as db:
        obj = db.get(EmailTemplate, tid)
        if not obj:
            await cq.answer("Уже удалён", show_alert=True)
        else:
            db.delete(obj)
            db.commit()
            await cq.answer("Удалён")
    await cq.message.edit_text("📑 TEMPLATES → список (нажмите для удаления):", reply_markup=_kb_list())


# ---------- helpers ----------
def parse_template_filename(name: str) -> Tuple[str, str]:
    """`olx.pl-g.html` → ('olx.pl', 'g') — шаблон под конкретную почту/рубрику
    (код = тип почты g/m/h/o/y = первая буква имени рубрики).
    `olx.pl.html` → ('olx.pl', '') — ОБЩИЙ шаблон сервиса (на все рубрики)."""
    name = (name or "").strip()
    m = _NAME_RE.match(name)
    if m:
        service = m.group("service").strip()
        code = m.group("code").strip().lower()
        if service and code:
            return service, code  # код хранится как mailbox шаблона
    m = _NAME_RE_PLAIN.match(name)
    if m:
        service = m.group("service").strip()
        if service:
            return service, ""  # '' = общий шаблон (на все рубрики)
    raise ValueError("имя: <code>service.html</code> или <code>service-код.html</code>")


def _upsert_template(service: str, mailbox: str, html: str, filename: str | None) -> bool:
    """Создаёт/обновляет шаблон. Возвращает True если создан, False если обновлён."""
    with Session(_engine) as db:
        existing = db.exec(
            select(EmailTemplate).where(
                (EmailTemplate.service == service) & (EmailTemplate.mailbox == mailbox)
            )
        ).first()
        if existing:
            existing.html = html
            existing.filename = filename
            existing.created_at = datetime.utcnow().isoformat()
            db.add(existing)
            db.commit()
            return False
        db.add(EmailTemplate(
            service=service, mailbox=mailbox, html=html,
            filename=filename, created_at=datetime.utcnow().isoformat(),
        ))
        db.commit()
        return True
