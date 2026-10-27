import logging
from datetime import datetime
from typing import List

from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select

from common.db import get_engine
from common.models.rubric import Rubric, RubricMailbox
from common.models.service_config import ServiceRubricConfig
from common.config import settings
from bot.fsm.admin import RubricAdd, RubricEdit, ServiceCfgEdit
from bot.services import mailboxes as mb
try:
    from common.constants import COUNTRIES as _COUNTRIES, COUNTRY_FLAGS as _FLAGS, COUNTRY_SERVICES as _CSVC
except Exception:  # pragma: no cover
    _COUNTRIES, _FLAGS, _CSVC = [], {}, {}

router = Router(name="rubrics-admin")
log = logging.getLogger("bot")
_engine = get_engine()

import re as _re
_DOM_SPLIT = _re.compile(r"[\s,;]+")


def rubric_for_email(email: str) -> dict | None:
    """По домену получателя вернуть настройки подходящей рубрики (или None)."""
    dom = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not dom:
        return None
    from common.domains import domain_in
    with Session(_engine) as db:
        for r in db.exec(select(Rubric).order_by(Rubric.id)).all():
            if domain_in(dom, r.domains or ""):
                return {
                    "id": r.id, "name": r.name, "smtp_group": r.smtp_group,
                    "shortener": r.shortener, "title": r.title, "sender_name": r.sender_name,
                }
    return None


# ---------- keyboards ----------
def kb_rubrics_root() -> InlineKeyboardMarkup:
    """Корень рубрик = сразу список рубрик, снизу «+ Добавить»."""
    rows: List[List[InlineKeyboardButton]] = []
    with Session(_engine) as db:
        rubrics = db.exec(select(Rubric).order_by(Rubric.id)).all()
    for r in rubrics:
        rows.append([InlineKeyboardButton(text=f"🗂 {r.name}", callback_data=f"rub:open:{r.id}")])
    rows.append([InlineKeyboardButton(text="+ Добавить рубрику", callback_data="rub:add")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="srv:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _kb_list() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    with Session(_engine) as db:
        rubrics = db.exec(select(Rubric).order_by(Rubric.id)).all()
    if not rubrics:
        rows.append([InlineKeyboardButton(text="Пусто — создайте рубрику", callback_data="rub:add")])
    else:
        for r in rubrics:
            rows.append([InlineKeyboardButton(text=f"🗂 {r.name}", callback_data=f"rub:open:{r.id}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="rub:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _rubric_view(rid: int):
    with Session(_engine) as db:
        r = db.get(Rubric, rid)
        if not r:
            return None, None
        mbs = db.exec(select(RubricMailbox).where(RubricMailbox.rubric_id == rid)).all()
        boxes = ", ".join(m.mailbox for m in mbs) or "—"
    doms = r.domains or "—"
    text = (
        f"🗂 <b>{r.name}</b>\n\n"
        f"• Домены получателей: {doms}\n"
        f"• Почты: {boxes}\n"
        f"• Тайтл: {r.title or '—'}\n"
        f"• Имя отправителя: {r.sender_name or '—'}\n"
        f"• SMTP-группа: {r.smtp_group or '—'}\n"
        f"• Сокращатель: {r.shortener or 'выкл'}"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✏️ Название", callback_data=f"rub:setname:{rid}"),
            InlineKeyboardButton(text="🌐 Домены", callback_data=f"rub:setdomains:{rid}"),
        ],
        [InlineKeyboardButton(text="✉️ Почты", callback_data=f"rub:mb:{rid}")],
        [
            InlineKeyboardButton(text=f"📮 SMTP: {r.smtp_group or '—'}", callback_data=f"rub:smtp:{rid}"),
            InlineKeyboardButton(text=f"🔗 Сокр: {r.shortener or 'выкл'}", callback_data=f"rub:short:{rid}"),
        ],
        [
            InlineKeyboardButton(text="🏷 Тайтл", callback_data=f"rub:settitle:{rid}"),
            InlineKeyboardButton(text="👤 Имя", callback_data=f"rub:setsender:{rid}"),
        ],
        [InlineKeyboardButton(text="🗑 Удалить рубрику", callback_data=f"rub:del:{rid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="rub:root")],
    ])
    return text, kb


def _kb_mailboxes(rid: int) -> InlineKeyboardMarkup:
    with Session(_engine) as db:
        mbs = db.exec(select(RubricMailbox).where(RubricMailbox.rubric_id == rid)).all()
    present = {m.mailbox: m.id for m in mbs}
    rows: List[List[InlineKeyboardButton]] = []
    for m in mbs:
        rows.append([InlineKeyboardButton(text=f"🗑 {m.mailbox}", callback_data=f"rub:mbdel:{rid}:{m.id}")])
    # добавить недостающие
    add_btns = [
        InlineKeyboardButton(text=f"+ {name}", callback_data=f"rub:mbadd:{rid}:{code}")
        for code, name in mb.pairs() if name not in present
    ]
    for i in range(0, len(add_btns), 3):
        rows.append(add_btns[i:i + 3])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"rub:open:{rid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------- entry ----------
async def show_rubrics_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("🗂 РУБРИКИ — выберите рубрику или добавьте:", reply_markup=kb_rubrics_root())


@router.callback_query(F.data == "rub:root")
async def rub_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("🗂 РУБРИКИ — выберите рубрику или добавьте:", reply_markup=kb_rubrics_root())
    except Exception:
        await cq.message.answer("🗂 РУБРИКИ — выберите рубрику или добавьте:", reply_markup=kb_rubrics_root())
    await cq.answer()


@router.callback_query(F.data == "rub:back")
async def rub_back(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    from bot.handlers.admin.index import back_to_admin
    await back_to_admin(cq)


# ---------- add rubric ----------
@router.callback_query(F.data == "rub:add")
async def rub_add(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.message.answer("Введите название рубрики:", reply_markup=InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="rub:root")]]
    ))
    await state.set_state(RubricAdd.name)
    await cq.answer()


@router.message(RubricAdd.name, F.text)
async def rub_add_name(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    name = (m.text or "").strip()
    if not name:
        await m.answer("Пустое название. Введите ещё раз:")
        return
    with Session(_engine) as db:
        r = Rubric(name=name, created_at=datetime.utcnow().isoformat())
        db.add(r)
        db.commit()
        db.refresh(r)
        rid = r.id
    await state.clear()
    text, kb = _rubric_view(rid)
    await m.answer(f"✅ Рубрика создана.\n\n{text}", reply_markup=kb)


# ---------- open / delete ----------
@router.callback_query(F.data.startswith("rub:open:"))
async def rub_open(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    rid = int(cq.data.split(":")[-1])
    text, kb = _rubric_view(rid)
    if text is None:
        await cq.answer("Рубрика не найдена", show_alert=True)
        return
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data == "rub:list")
async def rub_list(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("🗂 РУБРИКИ → список:", reply_markup=_kb_list())
    except Exception:
        await cq.message.answer("🗂 РУБРИКИ → список:", reply_markup=_kb_list())
    await cq.answer()


@router.callback_query(F.data.startswith("rub:del:"))
async def rub_delete(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    rid = int(cq.data.split(":")[-1])
    with Session(_engine) as db:
        for mb in db.exec(select(RubricMailbox).where(RubricMailbox.rubric_id == rid)).all():
            db.delete(mb)
        obj = db.get(Rubric, rid)
        if obj:
            db.delete(obj)
        db.commit()
    await cq.answer("Удалена")
    await cq.message.edit_text("🗂 РУБРИКИ → список:", reply_markup=_kb_list())


# ---------- mailboxes ----------
@router.callback_query(F.data.startswith("rub:mb:"))
async def rub_mailboxes(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    rid = int(cq.data.split(":")[-1])
    try:
        await cq.message.edit_text("✉️ Почты рубрики (+ добавить / 🗑 убрать):", reply_markup=_kb_mailboxes(rid))
    except Exception:
        await cq.message.answer("✉️ Почты рубрики:", reply_markup=_kb_mailboxes(rid))
    await cq.answer()


@router.callback_query(F.data.startswith("rub:mbadd:"))
async def rub_mailbox_add(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, rid, code = cq.data.split(":", 3)
    rid = int(rid)
    name = mb.code_to_name().get(code)
    if not name:
        await cq.answer("Неизвестная почта", show_alert=True)
        return
    with Session(_engine) as db:
        exists = db.exec(
            select(RubricMailbox).where(
                (RubricMailbox.rubric_id == rid) & (RubricMailbox.mailbox == name)
            )
        ).first()
        if not exists:
            db.add(RubricMailbox(rubric_id=rid, mailbox=name))
            db.commit()
    await cq.answer(f"+ {name}")
    await cq.message.edit_text("✉️ Почты рубрики (+ добавить / 🗑 убрать):", reply_markup=_kb_mailboxes(rid))


@router.callback_query(F.data.startswith("rub:mbdel:"))
async def rub_mailbox_del(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, rid, mbid = cq.data.split(":", 3)
    rid = int(rid)
    with Session(_engine) as db:
        obj = db.get(RubricMailbox, int(mbid))
        if obj:
            db.delete(obj)
            db.commit()
    await cq.answer("Убрана")
    await cq.message.edit_text("✉️ Почты рубрики (+ добавить / 🗑 убрать):", reply_markup=_kb_mailboxes(rid))


# ---------- bind smtp-group / shortener ----------
def _kb_pick(rid: int, prefix: str, options: list[tuple[str, str]]) -> InlineKeyboardMarkup:
    rows = [[InlineKeyboardButton(text=label, callback_data=f"{prefix}:{rid}:{val}")] for label, val in options]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"rub:open:{rid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("rub:smtp:"))
async def rub_smtp(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    rid = int(cq.data.split(":")[-1])
    # Только реальные SMTP-группы (gmail/main) — к ним привязаны SMTP-аккаунты.
    from bot.services.smtp_pool import SMTP_GROUPS, SMTP_GROUP_LABELS
    options = [(SMTP_GROUP_LABELS.get(g, g), g) for g in SMTP_GROUPS]
    await cq.message.edit_text(
        "📮 SMTP-группа рубрики (куда привязаны SMTP-аккаунты):",
        reply_markup=_kb_pick(rid, "rub:smtpset", options),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("rub:smtpset:"))
async def rub_smtp_set(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, rid, val = cq.data.split(":", 3)
    _set_field(int(rid), "smtp_group", val)
    await _reopen(cq, int(rid))


@router.callback_query(F.data.startswith("rub:short:"))
async def rub_short(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    rid = int(cq.data.split(":")[-1])
    await cq.message.edit_text(
        "🔗 Сокращатель рубрики:",
        reply_markup=_kb_pick(rid, "rub:shortset", [("n9", "n9"), ("Выключить", "off")]),
    )
    await cq.answer()


@router.callback_query(F.data.startswith("rub:shortset:"))
async def rub_short_set(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, rid, val = cq.data.split(":", 3)
    _set_field(int(rid), "shortener", None if val == "off" else val)
    await _reopen(cq, int(rid))


def _set_field(rid: int, field: str, value) -> None:
    with Session(_engine) as db:
        r = db.get(Rubric, rid)
        if r:
            setattr(r, field, value)
            db.add(r)
            db.commit()


async def _reopen(cq: CallbackQuery, rid: int) -> None:
    text, kb = _rubric_view(rid)
    if text is None:
        await cq.answer("Рубрика не найдена", show_alert=True)
        return
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer("Сохранено")


# ---------- edit title / sender ----------
@router.callback_query(F.data.startswith("rub:setname:"))
async def rub_set_name(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    rid = int(cq.data.split(":")[-1])
    await state.set_state(RubricEdit.value)
    await state.update_data(field="name", rid=rid)
    await cq.message.answer("Введите новое название рубрики:")
    await cq.answer()


@router.callback_query(F.data.startswith("rub:setdomains:"))
async def rub_set_domains(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    rid = int(cq.data.split(":")[-1])
    await state.set_state(RubricEdit.value)
    await state.update_data(field="domains", rid=rid)
    await cq.message.answer(
        "Введите домены получателей этой рубрики через пробел/запятую\n"
        "(напр. <code>gmx.net gmx.de web.de</code>), или /clear чтобы очистить:"
    )
    await cq.answer()


@router.callback_query(F.data.startswith("rub:settitle:"))
async def rub_set_title(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    rid = int(cq.data.split(":")[-1])
    await state.set_state(RubricEdit.value)
    await state.update_data(field="title", rid=rid)
    await cq.message.answer("Введите тайтл (тему) для рубрики (или /clear чтобы очистить):")
    await cq.answer()


@router.callback_query(F.data.startswith("rub:setsender:"))
async def rub_set_sender(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    rid = int(cq.data.split(":")[-1])
    await state.set_state(RubricEdit.value)
    await state.update_data(field="sender_name", rid=rid)
    await cq.message.answer("Введите имя отправителя для рубрики (или /clear чтобы очистить):")
    await cq.answer()


@router.message(RubricEdit.value, F.text)
async def rub_edit_value(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    data = await state.get_data()
    field = data.get("field")
    rid = data.get("rid")
    await state.clear()
    if field not in {"title", "sender_name", "name", "domains"} or not rid:
        await m.answer("Что-то пошло не так, попробуйте заново.")
        return
    val = (m.text or "").strip()
    if val == "/clear":
        val = None
    if field == "name" and not val:
        await m.answer("Название не может быть пустым. Введите ещё раз:")
        await state.set_state(RubricEdit.value)
        await state.update_data(field="name", rid=rid)
        return
    with Session(_engine) as db:
        r = db.get(Rubric, rid)
        if not r:
            await m.answer("Рубрика не найдена.")
            return
        setattr(r, field, val)
        db.add(r)
        db.commit()
    text, kb = _rubric_view(rid)
    await m.answer(f"✅ Сохранено.\n\n{text}", reply_markup=kb)


# ═══════════ SRVS: меню (Рубрики + Сервисы) ═══════════
def kb_srvs_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗂 Рубрики", callback_data="rub:root")],
        [InlineKeyboardButton(text="🧩 Сервисы (шаблон/тайтл/имя)", callback_data="svc:root")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="rub:back")],
    ])


async def show_srvs_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("🧩 SRVS:", reply_markup=kb_srvs_root())


@router.callback_query(F.data == "srv:root")
async def srv_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("🧩 SRVS:", reply_markup=kb_srvs_root())
    except Exception:
        await cq.message.answer("🧩 SRVS:", reply_markup=kb_srvs_root())
    await cq.answer()


def service_rubric_cfg(service: str, rubric_id: int, mailbox: str) -> dict | None:
    """Конфиг (sender/title/template) для (сервис, рубрика, почта). Сначала точная
    почта, затем любая (mailbox='')."""
    with Session(_engine) as db:
        for mb_key in (mailbox, ""):
            c = db.exec(
                select(ServiceRubricConfig).where(
                    (ServiceRubricConfig.service == service)
                    & (ServiceRubricConfig.rubric_id == rubric_id)
                    & (ServiceRubricConfig.mailbox == mb_key)
                )
            ).first()
            if c:
                return {"sender_name": c.sender_name, "title": c.title, "template_html": c.template_html}
    return None


# ───────── страна → сервис → рубрика → почта → {имя, тайтл, шаблон} ─────────
def _kb_countries() -> InlineKeyboardMarkup:
    btns = [InlineKeyboardButton(text=f"{_FLAGS.get(c, '')} {c}", callback_data=f"svc:c:{c}") for c in _COUNTRIES]
    rows = [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="srv:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "svc:root")
async def svc_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    await cq.message.edit_text("🧩 Сервисы — выберите страну:", reply_markup=_kb_countries())
    await cq.answer()


@router.callback_query(F.data.startswith("svc:c:"))
async def svc_country(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    code = cq.data.split(":")[-1]
    svcs = _CSVC.get(code, [])
    rows = [[InlineKeyboardButton(text=f"🔹 {s}", callback_data=f"svc:s:{code}:{s}")] for s in svcs] or \
           [[InlineKeyboardButton(text="— нет сервисов —", callback_data="svc:root")]]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="svc:root")])
    await cq.message.edit_text(f"🧩 {code} — выберите сервис:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


@router.callback_query(F.data.startswith("svc:s:"))
async def svc_service(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service = cq.data.split(":", 3)
    with Session(_engine) as db:
        rubrics = db.exec(select(Rubric).order_by(Rubric.id)).all()
    rows = [[InlineKeyboardButton(text=f"🗂 {r.name}", callback_data=f"svc:r:{code}:{service}:{r.id}")] for r in rubrics] or \
           [[InlineKeyboardButton(text="— нет рубрик —", callback_data="srv:root")]]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"svc:c:{code}")])
    await cq.message.edit_text(f"🧩 {service} — выберите рубрику:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


@router.callback_query(F.data.startswith("svc:r:"))
async def svc_rubric(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service, rid = cq.data.split(":", 4)
    rows = [[InlineKeyboardButton(text=f"✉️ {name}", callback_data=f"svc:m:{code}:{service}:{rid}:{name}")]
            for name in mb.names()]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"svc:s:{code}:{service}")])
    await cq.message.edit_text("🧩 Выберите почту:", reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await cq.answer()


def _svc_cfg_view(code: str, service: str, rid: int, mailbox: str):
    with Session(_engine) as db:
        r = db.get(Rubric, rid)
        cfg = db.exec(
            select(ServiceRubricConfig).where(
                (ServiceRubricConfig.service == service)
                & (ServiceRubricConfig.rubric_id == rid)
                & (ServiceRubricConfig.mailbox == mailbox)
            )
        ).first()
    rname = r.name if r else "?"
    sender = (cfg.sender_name if cfg and cfg.sender_name else None) or (r.sender_name if r else None)
    title = (cfg.title if cfg and cfg.title else None) or (r.title if r else None)
    tpl = "свой" if (cfg and cfg.template_html) else f"по рубрике ({service}-{(rname[:1] or '').lower()})"
    text = (
        f"🧩 <b>{service}</b> × <b>{rname}</b> × <b>{mailbox}</b>\n\n"
        f"• Имя отправителя: {sender or '—'}\n"
        f"• Тайтл: {title or '—'}\n"
        f"• Шаблон: {tpl}\n"
        f"<i>(пусто → дефолт рубрики)</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Имя", callback_data=f"svc:e:sn:{code}:{service}:{rid}:{mailbox}"),
            InlineKeyboardButton(text="🏷 Тайтл", callback_data=f"svc:e:st:{code}:{service}:{rid}:{mailbox}"),
        ],
        [InlineKeyboardButton(text="📄 Шаблон", callback_data=f"svc:e:tpl:{code}:{service}:{rid}:{mailbox}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"svc:r:{code}:{service}:{rid}")],
    ])
    return text, kb


@router.callback_query(F.data.startswith("svc:m:"))
async def svc_mailbox(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    _, _, code, service, rid, mailbox = cq.data.split(":", 5)
    text, kb = _svc_cfg_view(code, service, int(rid), mailbox)
    await cq.message.edit_text(text, reply_markup=kb)
    await cq.answer()


_FIELD_PROMPT = {
    "sn": ("sender_name", "Введите имя отправителя (или /clear):"),
    "st": ("title", "Введите тайтл (или /clear):"),
    "tpl": ("template_html", "Пришлите HTML-шаблон текстом или файлом (/clear — по рубрике):"),
}


@router.callback_query(F.data.startswith("svc:e:"))
async def svc_edit(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, fkey, code, service, rid, mailbox = cq.data.split(":", 6)
    field, prompt = _FIELD_PROMPT[fkey]
    await state.set_state(ServiceCfgEdit.value)
    await state.update_data(field=field, code=code, service=service, rid=int(rid), mailbox=mailbox)
    await cq.message.answer(prompt)
    await cq.answer()


def _save_cfg(service, rid, mailbox, field, val):
    with Session(_engine) as db:
        cfg = db.exec(
            select(ServiceRubricConfig).where(
                (ServiceRubricConfig.service == service)
                & (ServiceRubricConfig.rubric_id == rid)
                & (ServiceRubricConfig.mailbox == mailbox)
            )
        ).first()
        if not cfg:
            cfg = ServiceRubricConfig(service=service, rubric_id=rid, mailbox=mailbox)
        setattr(cfg, field, val)
        db.add(cfg)
        db.commit()


@router.message(ServiceCfgEdit.value, F.text)
async def svc_cfg_text(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    data = await state.get_data()
    await state.clear()
    field = data.get("field"); service = data.get("service"); rid = data.get("rid")
    mailbox = data.get("mailbox", ""); code = data.get("code", "")
    if field not in {"sender_name", "title", "template_html"} or not service or not rid:
        await m.answer("Что-то пошло не так.")
        return
    val = (m.text or "").strip()
    if val == "/clear":
        val = None
    _save_cfg(service, rid, mailbox, field, val)
    text, kb = _svc_cfg_view(code, service, rid, mailbox)
    await m.answer(f"✅ Сохранено.\n\n{text}", reply_markup=kb)


@router.message(ServiceCfgEdit.value, F.document)
async def svc_cfg_doc(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    data = await state.get_data()
    if data.get("field") != "template_html":
        await m.answer("Здесь ждут текст. Шаблон можно файлом только на шаге «Шаблон».")
        return
    await state.clear()
    service = data.get("service"); rid = data.get("rid"); mailbox = data.get("mailbox", ""); code = data.get("code", "")
    doc = m.document
    file = await m.bot.get_file(doc.file_id)
    content = await m.bot.download_file(file.file_path)
    html = content.read().decode("utf-8", errors="ignore")
    _save_cfg(service, rid, mailbox, "template_html", html)
    text, kb = _svc_cfg_view(code, service, rid, mailbox)
    await m.answer(f"✅ Шаблон сохранён.\n\n{text}", reply_markup=kb)
