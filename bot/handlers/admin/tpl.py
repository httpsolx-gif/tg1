import logging
from html import escape
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
from bot.fsm.admin import TemplateUpload, TemplateAssign
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


# ---------- кидок .html → сервис по имени → галочки рубрик (п.65) ----------
# Срабатывает без активного FSM (не перехватывает .txt для SMTP/нод, которые ждут файл
# в своих состояниях) и в режиме выбора рубрик (новый файл заменяет ожидающий).
_TPL_EXT = (".html", ".htm", ".txt")


def detect_service(filename: str) -> tuple[str | None, str, bool]:
    """Имя файла → (сервис, хвост после «сервис-», есть_в_каталоге).

    Сначала ищем самый длинный сервис каталога, с которого начинается имя
    (`testshop.de.html`, `testshop.de-s.html`, `testshop.de-promo.html`), иначе
    старый разбор `service[-код].html`.
    """
    name = (filename or "").strip()
    low = name.lower()
    base = low
    for ext in _TPL_EXT:
        if base.endswith(ext):
            base = base[: -len(ext)]
            break
    from common import catalog
    known = sorted({s for svcs in catalog.country_services().values() for s in svcs}, key=len, reverse=True)
    for svc in known:
        if base == svc:
            return svc, "", True
        if base.startswith(svc + "-") or base.startswith(svc + "_"):
            return svc, base[len(svc) + 1:], True
    try:
        svc, code = parse_template_filename(name)
        return svc.lower(), code, False
    except ValueError:
        return None, "", False


def _rubrics() -> list:
    from common.models.rubric import Rubric
    with Session(_engine) as db:
        rows = db.exec(select(Rubric).order_by(Rubric.id)).all()
        return [(r.id, r.name) for r in rows]


def _preselect(service: str, tail: str, filename: str) -> list[int]:
    """Что отметить сразу: где уже стоит этот же файл (перезаливка) + рубрики на букву-код
    из имени (старое соглашение `сервис-буква.html`)."""
    from common.models.service_config import ServiceRubricConfig as C
    sel: list[int] = []
    with Session(_engine) as db:
        for c in db.exec(select(C).where((C.service == service) & (C.template_name == filename))).all():
            sel.append(c.rubric_id or 0)
    if len(tail) == 1:
        sel += [rid for rid, name in _rubrics() if (name or "")[:1].lower() == tail]
    return sorted(set(sel))


def _kb_assign(sel: list[int]) -> InlineKeyboardMarkup:
    def mark(rid: int) -> str:
        return "✅" if rid in sel else "⬜"
    rubs = _rubrics()
    btns = [InlineKeyboardButton(text=f"{mark(rid)} {name}", callback_data=f"tpa:t:{rid}") for rid, name in rubs]
    rows = [[InlineKeyboardButton(text=f"{mark(0)} ALL — все рубрики (общий)", callback_data="tpa:t:0")]]
    rows += [btns[i:i + 2] for i in range(0, len(btns), 2)]
    if rubs:
        rows.append([InlineKeyboardButton(text="📋 Отметить все рубрики", callback_data="tpa:every")])
    rows.append([
        InlineKeyboardButton(text="✅ Сохранить", callback_data="tpa:save"),
        InlineKeyboardButton(text="✖️ Отмена", callback_data="tpa:cancel"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _assign_text(service: str, filename: str, in_catalog: bool) -> str:
    warn = "" if in_catalog else "\n⚠️ Такого сервиса нет в каталоге (🧩 Сервисы) — проверьте имя файла."
    return (f"📄 <code>{escape(filename)}</code> → сервис <b>{escape(service)}</b>{warn}\n\n"
            "Для каких рубрик поставить шаблон? Отметьте и нажмите «Сохранить».\n"
            "<i>ALL — общий для сервиса; рубрика с отметкой берёт свой и перекрывает ALL.</i>")


@router.message(StateFilter(None, TemplateAssign.pick), F.document)
async def tpl_drop_any(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    doc: Document = m.document
    name = (doc.file_name or "")
    if not name.lower().endswith(_TPL_EXT):
        return  # не похоже на шаблон — молча игнорируем
    service, tail, in_catalog = detect_service(name)
    if not service:
        await m.answer(f"📄 <code>{escape(name)}</code> — не понял сервис. "
                       "Назовите файл по сервису: <code>testshop.de.html</code> или <code>testshop.de-что-угодно.html</code>.")
        return
    file = await m.bot.get_file(doc.file_id)
    content = await m.bot.download_file(file.file_path)
    html = content.read().decode("utf-8", errors="ignore")
    sel = _preselect(service, tail, name)
    await state.set_state(TemplateAssign.pick)
    await state.update_data(tpl_service=service, tpl_html=html, tpl_name=name, tpl_sel=sel, tpl_cat=in_catalog)
    await m.answer(_assign_text(service, name, in_catalog), reply_markup=_kb_assign(sel))


@router.callback_query(TemplateAssign.pick, F.data.startswith("tpa:"))
async def tpl_assign_cb(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    data = await state.get_data()
    sel = set(data.get("tpl_sel") or [])
    act = cq.data.split(":")[1]
    if act == "cancel":
        await state.clear()
        await cq.message.edit_text("Шаблон не сохранён.")
        await cq.answer()
        return
    if act == "save":
        if not sel:
            await cq.answer("Отметьте хотя бы одну рубрику или ALL", show_alert=True)
            return
        from bot.handlers.admin.rubrics import set_template
        service, html, name = data["tpl_service"], data["tpl_html"], data["tpl_name"]
        names = dict(_rubrics())
        for rid in sorted(sel):
            set_template(service, rid, html, name)
        await state.clear()
        where = ", ".join(("ALL" if rid == 0 else escape(names.get(rid, f"#{rid}"))) for rid in sorted(sel))
        await cq.message.edit_text(f"✅ <code>{escape(name)}</code> → <b>{escape(service)}</b>: {where}")
        await cq.answer("Сохранено")
        return
    if act == "every":
        all_ids = {rid for rid, _ in _rubrics()}
        sel = (sel - all_ids) if all_ids <= sel else (sel | all_ids)  # повторное нажатие снимает
    elif act == "t":
        rid = int(cq.data.split(":")[2])
        sel ^= {rid}
    await state.update_data(tpl_sel=sorted(sel))
    try:
        await cq.message.edit_reply_markup(reply_markup=_kb_assign(sorted(sel)))
    except Exception:
        pass
    await cq.answer()


@router.callback_query(F.data.startswith("tpa:"))
async def tpl_assign_stale(cq: CallbackQuery):
    """Кнопки старого сообщения выбора (ожидание сброшено) — не молчим."""
    await cq.answer("Выбор устарел — киньте файл ещё раз", show_alert=True)


@router.message(TemplateAssign.pick, F.text)
async def tpl_assign_hint(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("Отметьте рубрики кнопками под файлом и нажмите «✅ Сохранить» (или «✖️ Отмена»).")


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
