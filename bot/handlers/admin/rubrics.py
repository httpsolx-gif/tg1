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
from bot.fsm.admin import RubricAdd, RubricEdit, RubricMailboxAdd, ServiceCfgEdit
try:
    from common.constants import COUNTRIES as _COUNTRIES, COUNTRY_FLAGS as _FLAGS, COUNTRY_SERVICES as _CSVC
except Exception:  # pragma: no cover
    _COUNTRIES, _FLAGS, _CSVC = [], {}, {}

router = Router(name="rubrics-admin")
log = logging.getLogger("bot")
_engine = get_engine()

from html import escape as _esc


def rubric_group(rub: dict) -> str:
    """SMTP-группа/target рубрики для SMTP/SHORT: имя рубрики (gmail/main), иначе старое поле, иначе main."""
    from bot.services.smtp_pool import SMTP_GROUPS
    name = (rub.get("name") or "").strip().lower()
    if name == BLACK_RUBRIC:
        return "black"  # у black своя SMTP-база области, без фолбэка на main/gmail
    if name in SMTP_GROUPS:
        return name
    return rub.get("smtp_group") or "main"


# Рубрика-«остальное»: ловит все домены, которых нет в почтах других рубрик
OTHER_RUBRIC = "other"
# Рубрика-«black»: профиль для black-отправителей. Её «Почты» = дроп-лист доменов
# получателей, на которые black НЕ слать. По домену НЕ подбирается (только по классу
# отправителя). Имя/тайтл/шаблон/SMTP/сокра — в «Сервисы» по этой рубрике.
BLACK_RUBRIC = "black"


def _rubric_dict(r: Rubric) -> dict:
    return {"id": r.id, "name": r.name, "smtp_group": r.smtp_group, "title": r.title, "sender_name": r.sender_name}


def rubric_for_email(email: str) -> dict | None:
    """По домену получателя вернуть рубрику, в чьих почтах он есть; иначе рубрику «other» (или None)."""
    dom = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not dom:
        return None
    from common.domains import domain_in
    other = None
    by_name: dict[str, Rubric] = {}
    with Session(_engine) as db:
        for r in db.exec(select(Rubric).order_by(Rubric.id)).all():
            nm = (r.name or "").strip().lower()
            by_name.setdefault(nm, r)
            if nm == BLACK_RUBRIC:
                continue  # black — только по классу отправителя, не по домену получателя
            if nm == OTHER_RUBRIC:
                other = other or r
            mbs = db.exec(select(RubricMailbox.mailbox).where(RubricMailbox.rubric_id == r.id)).all()
            if domain_in(dom, " ".join(mbs)):
                return _rubric_dict(r)
        # корп/кастомный домен — по MX определить реального провайдера и его рубрику
        try:
            from bot.services.mxlookup import rubric_name_for_domain
            rn = rubric_name_for_domain(dom)
            if rn and rn in by_name and rn not in (BLACK_RUBRIC,):
                return _rubric_dict(by_name[rn])
        except Exception:
            pass
        return _rubric_dict(other) if other else None


def _get_black_rubric(db) -> Rubric | None:
    for r in db.exec(select(Rubric).order_by(Rubric.id)).all():
        if (r.name or "").strip().lower() == BLACK_RUBRIC:
            return r
    return None


def ensure_black_rubric() -> dict:
    """Гарантировать наличие зарезервированной рубрики black (создать при отсутствии)."""
    with Session(_engine) as db:
        r = _get_black_rubric(db)
        if not r:
            r = Rubric(name=BLACK_RUBRIC, created_at=datetime.utcnow().isoformat())
            db.add(r)
            db.commit()
            db.refresh(r)
        return _rubric_dict(r)


def black_denied(email: str) -> bool:
    """True, если домен получателя в дроп-листе black-рубрики («не слать на»)."""
    dom = (email or "").rsplit("@", 1)[-1].strip().lower()
    if not dom:
        return False
    from common.domains import domain_in
    with Session(_engine) as db:
        r = _get_black_rubric(db)
        if not r:
            return False
        mbs = db.exec(select(RubricMailbox.mailbox).where(RubricMailbox.rubric_id == r.id)).all()
    return bool(mbs) and domain_in(dom, " ".join(mbs))


def _is_black_rid(rid: int) -> bool:
    with Session(_engine) as db:
        r = db.get(Rubric, rid)
        return bool(r and (r.name or "").strip().lower() == BLACK_RUBRIC)


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
        rname = r.name
        mbs = db.exec(select(RubricMailbox).where(RubricMailbox.rubric_id == rid)).all()
        boxes = _esc(", ".join(m.mailbox for m in mbs)) or "—"
    nm = (rname or "").strip().lower()
    if nm == BLACK_RUBRIC:
        text = (
            f"⚫️ <b>{rname}</b> — профиль для <b>black</b>-отправителей\n\n"
            f"• 🚫 Не слать на: {boxes}\n\n"
            "<i>Домены получателей, на которые black-отправителям письма не уходят. "
            "Имя/тайтл/шаблон/SMTP/сокращатель для black настраиваются в 🧩 Сервисы "
            "по рубрике «black».</i>"
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🚫 Не слать на", callback_data=f"rub:mb:{rid}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="rub:root")],
        ])
        return text, kb
    catch_all = "• Сюда попадают ВСЕ домены, которых нет в почтах других рубрик.\n" \
        if nm == OTHER_RUBRIC else ""
    text = (
        f"🗂 <b>{rname}</b>\n\n"
        f"• Почты: {boxes}\n{catch_all}\n"
        f"<i>По почтам выбирается рубрика при отправке. SMTP и сокращатель — в разделах SMTP / SHORT, "
        f"тайтл и имя отправителя — в «Сервисы».</i>"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✉️ Почты", callback_data=f"rub:mb:{rid}"),
            InlineKeyboardButton(text="✏️ Название", callback_data=f"rub:setname:{rid}"),
        ],
        [InlineKeyboardButton(text="🗑 Удалить рубрику", callback_data=f"rub:del:{rid}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="rub:root")],
    ])
    return text, kb


def _kb_mailboxes(rid: int) -> InlineKeyboardMarkup:
    with Session(_engine) as db:
        mbs = db.exec(select(RubricMailbox).where(RubricMailbox.rubric_id == rid)).all()
    rows: List[List[InlineKeyboardButton]] = []
    for m in mbs:
        rows.append([InlineKeyboardButton(text=f"🗑 {m.mailbox}", callback_data=f"rub:mbdel:{rid}:{m.id}")])
    rows.append([InlineKeyboardButton(text="+ добавить", callback_data=f"rub:mbadd:{rid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"rub:open:{rid}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def parse_mailbox_list(text: str) -> list[str]:
    """`web, GMX ,gmail.com,,web` → ['web', 'gmx', 'gmail.com'] (порядок сохраняется)."""
    out: list[str] = []
    for part in (text or "").split(","):
        v = part.strip().lower()
        if v and v not in out:
            out.append(v)
    return out


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
    if _is_black_rid(rid):
        await cq.answer("Рубрику black удалять нельзя — это профиль black-отправителей.", show_alert=True)
        return
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
    title = ("🚫 Не слать на — домены получателей (+ добавить / 🗑 убрать):"
             if _is_black_rid(rid) else "✉️ Почты рубрики (+ добавить / 🗑 убрать):")
    try:
        await cq.message.edit_text(title, reply_markup=_kb_mailboxes(rid))
    except Exception:
        await cq.message.answer(title, reply_markup=_kb_mailboxes(rid))
    await cq.answer()


@router.callback_query(F.data.startswith("rub:mbadd:"))
async def rub_mailbox_add(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    rid = int(cq.data.split(":")[2])
    await state.set_state(RubricMailboxAdd.waiting)
    await state.update_data(rid=rid)
    if _is_black_rid(rid):
        await cq.message.answer(
            "Отправьте домены получателей, на которые <b>black НЕ слать</b>, через запятую\n"
            "(напр. <code>gmail.com, yahoo.com, web.de</code>):"
        )
    else:
        await cq.message.answer(
            "Отправьте почты одним сообщением через запятую — любые значения\n"
            "(напр. <code>web, gmx, gmail.com, t-online</code>):"
        )
    await cq.answer()


@router.message(RubricMailboxAdd.waiting, F.text)
async def rub_mailbox_add_text(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    rid = (await state.get_data()).get("rid")
    values = parse_mailbox_list(m.text)
    if not values:
        await m.answer("Пусто. Отправьте почты через запятую (напр. <code>web, gmx</code>):")
        return
    await state.clear()
    with Session(_engine) as db:
        if not rid or not db.get(Rubric, rid):
            await m.answer("Рубрика не найдена.")
            return
        present = {
            x.mailbox.lower()
            for x in db.exec(select(RubricMailbox).where(RubricMailbox.rubric_id == rid)).all()
        }
        added = [v for v in values if v not in present]
        for v in added:
            db.add(RubricMailbox(rubric_id=rid, mailbox=v))
        db.commit()
    skipped = len(values) - len(added)
    msg = f"✅ Добавлено: {_esc(', '.join(added))}" if added else "Ничего нового не добавлено"
    if skipped:
        msg += f" (уже были: {skipped})"
    await m.answer(f"{msg}\n\n✉️ Почты рубрики (+ добавить / 🗑 убрать):", reply_markup=_kb_mailboxes(rid))


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


# ---------- edit name ----------
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


@router.message(RubricEdit.value, F.text)
async def rub_edit_value(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    data = await state.get_data()
    field = data.get("field")
    rid = data.get("rid")
    await state.clear()
    if field != "name" or not rid:
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
    rows = [[InlineKeyboardButton(text="🌐 ALL (все страны)", callback_data="sco:o:*:*:0")]]
    rows += [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="srv:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data == "svc:root")
async def svc_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text("🧩 Сервисы — выберите страну:", reply_markup=_kb_countries())
    except Exception:
        await cq.message.answer("🧩 Сервисы — выберите страну:", reply_markup=_kb_countries())
    await cq.answer()


@router.callback_query(F.data.startswith("svc:c:"))
async def svc_country(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    code = cq.data.split(":")[-1]
    svcs = _CSVC.get(code, [])
    rows = [[InlineKeyboardButton(text=f"🌐 ALL (все сервисы {code})", callback_data=f"sco:o:{code}:*:0")]]
    rows += [[InlineKeyboardButton(text=f"🔹 {s}", callback_data=f"svc:s:{code}:{s}")] for s in svcs]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="svc:root")])
    _kb = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(f"🧩 {code} — выберите сервис:", reply_markup=_kb)
    except Exception:
        await cq.message.answer(f"🧩 {code} — выберите сервис:", reply_markup=_kb)
    await cq.answer()


@router.callback_query(F.data.startswith("svc:s:"))
async def svc_service(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service = cq.data.split(":", 3)
    with Session(_engine) as db:
        rubrics = db.exec(select(Rubric).order_by(Rubric.id)).all()
    rows = [[InlineKeyboardButton(text="🌐 ALL (все рубрики)", callback_data=f"sco:o:{code}:{service}:0")]]
    rows += [[InlineKeyboardButton(text=f"🗂 {r.name}", callback_data=f"svc:r:{code}:{service}:{r.id}")] for r in rubrics]
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"svc:c:{code}")])
    _kb = InlineKeyboardMarkup(inline_keyboard=rows)
    try:
        await cq.message.edit_text(f"🧩 {service} — выберите рубрику:", reply_markup=_kb)
    except Exception:
        await cq.message.answer(f"🧩 {service} — выберите рубрику:", reply_markup=_kb)
    await cq.answer()


@router.callback_query(F.data.startswith("svc:r:"))
async def svc_rubric(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    _, _, code, service, rid = cq.data.split(":", 4)
    # Без выбора почты: настройка оверрайда сразу на всю рубрику (mailbox='')
    text, kb = _svc_cfg_view(code, service, int(rid), "")
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
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
        f"🧩 <b>{service}</b> × <b>{rname}</b>  <i>(вся рубрика)</i>\n\n"
        f"• Имя отправителя: {sender or '—'}\n"
        f"• Тайтл: {title or '—'}\n"
        f"• Шаблон: {tpl}\n"
        f"<i>(пусто → дефолт рубрики)</i>"
    )
    from bot.handlers.admin import scope_ops
    from bot.services.scope import get_or_create
    with Session(_engine) as db:
        sid = get_or_create(db, code, service, rid).id
    text += "\n\n" + scope_ops.ops_text(sid)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="👤 Имя", callback_data=f"svc:e:sn:{code}:{service}:{rid}:{mailbox}"),
            InlineKeyboardButton(text="🏷 Тайтл", callback_data=f"svc:e:st:{code}:{service}:{rid}:{mailbox}"),
        ],
        [InlineKeyboardButton(text="📄 Шаблон", callback_data=f"svc:e:tpl:{code}:{service}:{rid}:{mailbox}")],
        *scope_ops.ops_rows(sid),
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"svc:s:{code}:{service}")],
    ])
    return text, kb


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
