import logging
import re
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
from bot.fsm.admin import RubricAdd, RubricEdit, RubricMailboxAdd, ServiceCfgEdit, CatalogEdit, UniqTimes
from common import catalog  # страны/сервисы — каталог в БД (п.61)
from html import escape as _esc

router = Router(name="rubrics-admin")
log = logging.getLogger("bot")
_engine = get_engine()


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
    rows.append([InlineKeyboardButton(text="➕ Добавить рубрику", callback_data="rub:add")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
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
    rows.append([InlineKeyboardButton(text="➕ добавить", callback_data=f"rub:mbadd:{rid}")])
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


# ═══════════ SRVS = сразу Сервисы (страны); Рубрики — отдельный раздел админки (п.86) ═══════════
def kb_srvs_root() -> InlineKeyboardMarkup:
    return _kb_countries()


async def show_srvs_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer(_COUNTRIES_TXT, reply_markup=kb_srvs_root())


@router.callback_query(F.data == "srv:root")
async def srv_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text(_COUNTRIES_TXT, reply_markup=kb_srvs_root())
    except Exception:
        await cq.message.answer(_COUNTRIES_TXT, reply_markup=kb_srvs_root())
    await cq.answer()


_CFG_FIELDS = ("sender_name", "title", "template_html", "uniq_opts")


def _cfg_row(db: Session, service: str, rid: int | None, mailbox: str):
    """Строка оверрайда; rid 0/None = ALL рубрик сервиса (rubric_id IS NULL)."""
    rcond = (ServiceRubricConfig.rubric_id == rid) if rid else (ServiceRubricConfig.rubric_id == None)  # noqa: E711
    return db.exec(select(ServiceRubricConfig).where(
        (ServiceRubricConfig.service == service) & rcond & (ServiceRubricConfig.mailbox == mailbox)
    )).first()


def service_cfg_layers(service: str, rubric_id: int | None, mailbox: str) -> dict:
    """{поле: (значение, источник)} — источник 'rubric' | 'all'; поле без значения отсутствует.

    Порядок по КАЖДОМУ полю: рубрика (точная почта → любая) → ALL рубрик (точная → любая).
    Раньше бралась первая найденная строка целиком, и пустое поле в ней не добиралось ниже.
    """
    out: dict = {}
    layers = ([(rubric_id, mailbox, "rubric"), (rubric_id, "", "rubric")] if rubric_id else []) \
        + [(None, mailbox, "all"), (None, "", "all")]
    with Session(_engine) as db:
        for rid, mb, src in layers:
            c = _cfg_row(db, service, rid, mb)
            if not c:
                continue
            for f in _CFG_FIELDS:
                if f not in out and getattr(c, f):
                    out[f] = (getattr(c, f), src)
    return out


def service_rubric_cfg(service: str, rubric_id: int, mailbox: str) -> dict | None:
    """Конфиг (sender/title/template) для отправки: рубрика → ALL рубрик сервиса (по полям)."""
    layers = service_cfg_layers(service, rubric_id, mailbox)
    return {f: v for f, (v, _src) in layers.items()} or None


# ───────── страна → сервис → рубрика → почта → {имя, тайтл, шаблон} ─────────
def _kb_countries() -> InlineKeyboardMarkup:
    cs = catalog.country_services()
    # число сервисов рядом со страной: пустые (0) в отправке скрыты
    btns = [InlineKeyboardButton(text=f"{c.flag} {c.code} · {len(cs.get(c.code, []))}", callback_data=f"svc:c:{c.code}")
            for c in catalog.countries()]
    rows = [[InlineKeyboardButton(text="🌐 ALL", callback_data="sco:o:*:*:0")]]
    rows += [btns[i:i + 3] for i in range(0, len(btns), 3)]
    rows.append([InlineKeyboardButton(text="➕ Добавить страну", callback_data="svc:cadd")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


_COUNTRIES_TXT = ("🧩 Сервисы — выберите страну.\n"
                  "<i>Цифра — сколько у страны сервисов. Страны с 0 в отправке не показываются.</i>")


@router.callback_query(F.data == "svc:root")
async def svc_root(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    try:
        await cq.message.edit_text(_COUNTRIES_TXT, reply_markup=_kb_countries())
    except Exception:
        await cq.message.answer(_COUNTRIES_TXT, reply_markup=_kb_countries())
    await cq.answer()


@router.callback_query(F.data.startswith("svc:c:"))
async def svc_country(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    code = cq.data.split(":")[-1]
    text, _kb = _country_view(code)
    try:
        await cq.message.edit_text(text, reply_markup=_kb)
    except Exception:
        await cq.message.answer(text, reply_markup=_kb)
    await cq.answer()


def _country_view(code: str):
    svcs = catalog.services(code)
    rows = [[InlineKeyboardButton(text="🌐 ALL", callback_data=f"sco:o:{code}:*:0")]]
    rows += [[InlineKeyboardButton(text=f"🔹 {s}", callback_data=f"svc:s:{code}:{s}")] for s in svcs]
    rows.append([InlineKeyboardButton(text="➕ Добавить", callback_data=f"svc:sadd:{code}")])
    rows.append([InlineKeyboardButton(text="🗑 Удалить страну", callback_data=f"svc:cdel:{code}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="svc:root")])
    hint = "" if svcs else "\n<i>Сервисов нет — в отправке страна скрыта.</i>"
    return f"🧩 {catalog.country_flag(code)} {code} — выберите сервис:{hint}", InlineKeyboardMarkup(inline_keyboard=rows)


@router.callback_query(F.data.startswith("svc:s:"))
async def svc_service(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service = cq.data.split(":", 3)
    with Session(_engine) as db:
        rubrics = db.exec(select(Rubric).order_by(Rubric.id)).all()
    rows = [[InlineKeyboardButton(text="🌐 ALL", callback_data=f"sco:o:{code}:{service}:0")]]
    rows += [[InlineKeyboardButton(text=f"🗂 {r.name}", callback_data=f"svc:r:{code}:{service}:{r.id}")] for r in rubrics]
    rows.append([InlineKeyboardButton(text="🗑 Удалить сервис", callback_data=f"svc:sdel:{code}:{service}")])
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


def set_template(service: str, rid: int, html: str, name: str | None) -> None:
    """Поставить шаблон рубрике сервиса (rid=0 — ALL рубрик). п.65"""
    with Session(_engine) as db:
        cfg = _cfg_row(db, service, rid, "")
        if not cfg:
            cfg = ServiceRubricConfig(service=service, rubric_id=rid or None, mailbox="")
        cfg.template_html = html
        cfg.template_name = name
        db.add(cfg)
        db.commit()


def clear_template(service: str, rid: int) -> bool:
    with Session(_engine) as db:
        cfg = _cfg_row(db, service, rid, "")
        if not cfg or not cfg.template_html:
            return False
        cfg.template_html = None
        cfg.template_name = None
        db.add(cfg)
        db.commit()
        return True


def _own_template_name(service: str, rid: int) -> str | None:
    """Имя файла шаблона, заданного именно на этом уровне (рубрика или ALL), иначе None."""
    with Session(_engine) as db:
        cfg = _cfg_row(db, service, rid, "")
        if cfg and cfg.template_html:
            return cfg.template_name or "без имени"
    return None


def _tpl_file_info(service: str, code: str | None, lay: dict, rid: int = 0) -> str:
    """Какой шаблон уйдёт. Ставится кидком .html в чат + галочки рубрик (п.65)."""
    from bot.services.templates import resolve_template_html
    if "template_html" in lay:
        src = lay["template_html"][1]
        # имя берём с того уровня, откуда шаблон пришёл (рубрика или ALL)
        name = _own_template_name(service, rid if src == "rubric" else 0) or "без имени"
        return f"<code>{_esc(name)}</code>" + (" <i>(из ALL)</i>" if src == "all" and rid else "")
    s = _esc(service)
    # старая библиотека файлов (сервис-буква.html / сервис.html) — пока действует как фолбэк
    if code is not None and resolve_template_html(service, code) is not None:
        return f"<code>{s}-{code}.html</code> <i>(старая загрузка)</i>"
    if resolve_template_html(service, "") is not None:
        return f"<code>{s}.html</code> <i>(общий, старая загрузка)</i>"
    return "стандартный — киньте .html в чат и отметьте рубрики"


def _svc_cfg_view(code: str, service: str, rid: int, mailbox: str):
    """Карточка сервиса: имя/тайтл/шаблон + ops области. rid=0 — ALL рубрик сервиса."""
    with Session(_engine) as db:
        r = db.get(Rubric, rid) if rid else None
    lay = service_cfg_layers(service, rid or None, mailbox)
    if rid:
        rname = r.name if r else "?"
        defaults = {"sender_name": r.sender_name if r else None, "title": r.title if r else None}

        def show(f: str) -> str:
            if f in lay:
                v, src = lay[f]
                return _esc(v) + (" <i>(из ALL)</i>" if src == "all" else "")
            return (_esc(defaults[f]) + " <i>(рубрики)</i>") if defaults.get(f) else "—"
        tpl = _tpl_file_info(service, (rname[:1] or "").lower(), lay, rid)
        head = f"🧩 <b>{_esc(service)}</b> × <b>{_esc(rname)}</b>  <i>(вся рубрика)</i>"
        foot = "<i>(пусто → ALL сервиса → дефолт рубрики)</i>"
    else:
        def show(f: str) -> str:
            return _esc(lay[f][0]) if f in lay else "— <i>(у каждой рубрики своё)</i>"
        tpl = _tpl_file_info(service, None, lay, 0)
        head = f"🧩 <b>{_esc(code)} · {_esc(service)}</b> × <b>ALL</b>  <i>(все рубрики)</i>"
        foot = "<i>Действует на все рубрики сервиса; рубрика со своим значением его перекрывает.</i>"
    text = (
        f"{head}\n\n"
        f"• Имя отправителя: {show('sender_name')}\n"
        f"• Тайтл: {show('title')}\n"
        f"• Шаблон: {tpl}\n"
        f"{foot}"
    )
    from bot.handlers.admin import scope_ops
    from bot.services.scope import get_or_create
    with Session(_engine) as db:
        sid = get_or_create(db, code, service, rid).id
    text += "\n\n" + scope_ops.ops_text(sid)
    ops = scope_ops.ops_rows(sid)
    # порядок: Автосмена → Имя | Тайтл → SMTP | SHORT
    kb = InlineKeyboardMarkup(inline_keyboard=[
        ops[0],
        [
            InlineKeyboardButton(text="👤 Имя", callback_data=f"svc:e:sn:{code}:{service}:{rid}:{mailbox}"),
            InlineKeyboardButton(text="🏷 Тайтл", callback_data=f"svc:e:st:{code}:{service}:{rid}:{mailbox}"),
        ],
        [InlineKeyboardButton(text=_uniq_btn_label(service, rid, mailbox),
                              callback_data=f"uniq:o:{code}:{service}:{rid}:{mailbox}")],
        *([[InlineKeyboardButton(text="🗑 Шаблон", callback_data=f"svc:tpldel:{code}:{service}:{rid}")]]
          if _own_template_name(service, rid) else []),
        *ops[1:],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"svc:s:{code}:{service}")],
    ])
    return text, kb


_FIELD_PROMPT = {
    "sn": ("sender_name", "Введите имя отправителя (или /clear):"),
    "st": ("title", "Введите тайтл (или /clear):"),
}  # шаблон — только файлом в чат (п.64), отдельного ввода HTML здесь нет


@router.callback_query(F.data.startswith("svc:e:"))
async def svc_edit(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, fkey, code, service, rid, mailbox = cq.data.split(":", 6)
    if fkey not in _FIELD_PROMPT:  # старая кнопка «📄 Шаблон» в истории чата
        await cq.answer("Шаблон — просто киньте файл в чат: сервис-буква.html или сервис.html", show_alert=True)
        return
    field, prompt = _FIELD_PROMPT[fkey]
    await state.set_state(ServiceCfgEdit.value)
    await state.update_data(field=field, code=code, service=service, rid=int(rid), mailbox=mailbox)
    await cq.message.answer(prompt)
    await cq.answer()


def _save_cfg(service, rid, mailbox, field, val):
    with Session(_engine) as db:
        cfg = _cfg_row(db, service, rid, mailbox)
        if not cfg:
            cfg = ServiceRubricConfig(service=service, rubric_id=rid or None, mailbox=mailbox)
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
    if field not in {"sender_name", "title"} or not service or rid is None:
        await m.answer("Что-то пошло не так.")
        return
    val = (m.text or "").strip()
    if val == "/clear":
        val = None
    _save_cfg(service, rid, mailbox, field, val)
    text, kb = _svc_cfg_view(code, service, rid, mailbox)
    await m.answer(f"✅ Сохранено.\n\n{text}", reply_markup=kb)


@router.message(ServiceCfgEdit.value, F.document)
async def svc_cfg_doc(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("Здесь ждут текст (имя/тайтл). Шаблон — киньте файл в чат вне ввода: "
                   "<code>сервис-буква.html</code> или <code>сервис.html</code>.")


@router.callback_query(F.data.startswith("svc:tpldel:"))
async def svc_tpl_del(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service, rid = cq.data.split(":", 4)
    ok = clear_template(service, int(rid))
    text, kb = _svc_cfg_view(code, service, int(rid), "")
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        pass
    await cq.answer("Шаблон убран" if ok else "Шаблона уже нет")


# ═══════════════════ 🧬 Уникализация HTML-шаблона (htmlmix, п.106/107) ═══════════════════
def _uniq_cfg_load(service: str, rid, mailbox: str) -> dict:
    """Настройки уникализации для связки (слоятся рубрика→ALL, как имя/тайтл)."""
    from bot.services import uniq
    lay = service_cfg_layers(service, int(rid) or None, mailbox)
    raw = lay["uniq_opts"][0] if "uniq_opts" in lay else None
    return uniq.parse_cfg(raw)


def _uniq_btn_label(service: str, rid, mailbox: str) -> str:
    from bot.services import uniq
    cfg = _uniq_cfg_load(service, rid, mailbox)
    if cfg.get("on") and uniq.enabled_count(cfg):
        return f"🧬 Уникализация: вкл · {uniq.enabled_count(cfg)} опц."
    return "🧬 Уникализация: выкл"


def _rubrics_with_own_template(service: str) -> list[str]:
    """Рубрики сервиса, у которых есть СВОЙ шаблон (оверрайд или в библиотеке)."""
    from bot.services.templates import resolve_template_html
    out: list[str] = []
    with Session(_engine) as db:
        rubs = [(r.id, r.name) for r in db.exec(select(Rubric)).all()]
    for rid_, name in rubs:
        if name == "black":
            continue
        code_ = (name[:1] or "").lower()
        with Session(_engine) as db:
            row = _cfg_row(db, service, rid_, "")
        if (row and row.template_html) or resolve_template_html(service, code_) is not None:
            out.append(name)
    return out


def _uniq_view(code: str, service: str, rid: int, mailbox: str):
    from bot.services import uniq, keys
    cfg = _uniq_cfg_load(service, rid, mailbox)
    on = bool(cfg.get("on"))
    opts = cfg.get("opts") or {}
    tok = "✅ задан" if keys.get("htmlmix") else "❌ нет (OTHER → 🔑 Ключи)"
    scope = "ALL рубрик" if not int(rid) else "рубрика"
    # На ALL уникализация применится, только если на ALL задан свой шаблон. Если у рубрик
    # свои шаблоны — вариант из ALL (станд. шаблон) при отправке не совпадёт → предупреждаем.
    warn = ""
    if not int(rid):
        own = _rubrics_with_own_template(service)
        if own:
            warn = ("ℹ️ У рубрик свои шаблоны (" + ", ".join(_esc(n) for n in own) +
                    "). На «ALL» уникализация применится к каждой по её шаблону — варианты "
                    "готовятся фоном (по одной рубрике в минуту, лимит API).\n\n")
    sched = cfg.get("mode") == "schedule"
    mode_txt = ("по расписанию: " + ", ".join(cfg.get("times") or [])) if sched else "вручную (кнопкой)"
    status = uniq.status_line(service, int(rid) or None, mailbox, cfg)
    text = (
        f"🧬 <b>Уникализация шаблона</b> — {_esc(service)} × {scope}\n\n"
        f"{warn}"
        f"• Статус: <b>{'🟢 включена' if on else '🔴 выключена'}</b>\n"
        f"• Токен htmlmix: {tok}\n"
        f"• Обновление: <b>{mode_txt}</b>\n"
        f"• Текущий вариант: {status}\n\n"
        "<i>Шаблон уникализируется РАЗОМ и используется на всех письмах до следующего "
        "обновления (вручную кнопкой или по расписанию). Нет варианта / API недоступен / "
        "сменились шаблон или опции → письмо уходит с исходным шаблоном. Время расписания — "
        "по часам сервера (на проде UTC). «⚠️» — опция меняет видимый текст.</i>"
    )
    rows = [[InlineKeyboardButton(
        text=("🟢 Включена — выключить" if on else "🔴 Выключена — включить"),
        callback_data=f"uniq:on:{code}:{service}:{rid}:{mailbox}")]]
    for i, (k, _g, lbl, _d) in enumerate(uniq.OPTIONS):
        mark = "☑️" if opts.get(k) else "⬜️"
        rows.append([InlineKeyboardButton(text=f"{mark} {lbl}",
                                          callback_data=f"uniq:t:{i}:{code}:{service}:{rid}:{mailbox}")])
    rows.append([InlineKeyboardButton(
        text=f"🔁 Режим: {'по расписанию' if sched else 'вручную'}",
        callback_data=f"uniq:mode:{code}:{service}:{rid}:{mailbox}")])
    if sched:
        rows.append([InlineKeyboardButton(text="🕐 Время обновления",
                                          callback_data=f"uniq:time:{code}:{service}:{rid}:{mailbox}")])
    rows.append([InlineKeyboardButton(text="🔁 Сгенерировать сейчас",
                                      callback_data=f"uniq:gen:{code}:{service}:{rid}:{mailbox}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад",
                                      callback_data=f"uniq:back:{code}:{service}:{rid}:{mailbox}")])
    return text, InlineKeyboardMarkup(inline_keyboard=rows)


def _uniq_save(service: str, rid, mailbox: str, cfg: dict) -> None:
    from bot.services import uniq
    _save_cfg(service, int(rid), mailbox, "uniq_opts", uniq.dump_cfg(cfg))


async def _uniq_show(cq: CallbackQuery, code, service, rid, mailbox, note: str | None = None):
    text, kb = _uniq_view(code, service, int(rid), mailbox)
    if note:
        text = note + "\n\n" + text
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("uniq:o:"))
async def uniq_open(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    _, _, code, service, rid, mailbox = cq.data.split(":", 5)
    await _uniq_show(cq, code, service, rid, mailbox)
    await cq.answer()


@router.callback_query(F.data.startswith("uniq:on:"))
async def uniq_toggle_on(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service, rid, mailbox = cq.data.split(":", 5)
    cfg = _uniq_cfg_load(service, rid, mailbox)
    cfg["on"] = not cfg.get("on")
    _uniq_save(service, rid, mailbox, cfg)
    await _uniq_show(cq, code, service, rid, mailbox)
    await cq.answer("Включено" if cfg["on"] else "Выключено")


@router.callback_query(F.data.startswith("uniq:t:"))
async def uniq_toggle_opt(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    from bot.services import uniq
    _, _, idx, code, service, rid, mailbox = cq.data.split(":", 6)
    cfg = _uniq_cfg_load(service, rid, mailbox)
    try:
        key = uniq.OPTIONS[int(idx)][0]
    except (ValueError, IndexError):
        await cq.answer("?", show_alert=True)
        return
    cfg["opts"][key] = 0 if cfg["opts"].get(key) else 1
    _uniq_save(service, rid, mailbox, cfg)
    await _uniq_show(cq, code, service, rid, mailbox)
    await cq.answer(("вкл " if cfg["opts"][key] else "выкл ") + uniq._LABEL.get(key, key))


@router.callback_query(F.data.startswith("uniq:mode:"))
async def uniq_mode(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service, rid, mailbox = cq.data.split(":", 5)
    cfg = _uniq_cfg_load(service, rid, mailbox)
    cfg["mode"] = "manual" if cfg.get("mode") == "schedule" else "schedule"
    _uniq_save(service, rid, mailbox, cfg)
    await _uniq_show(cq, code, service, rid, mailbox)
    await cq.answer("По расписанию" if cfg["mode"] == "schedule" else "Вручную")


@router.callback_query(F.data.startswith("uniq:time:"))
async def uniq_time_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service, rid, mailbox = cq.data.split(":", 5)
    cfg = _uniq_cfg_load(service, rid, mailbox)
    await state.set_state(UniqTimes.value)
    await state.update_data(code=code, service=service, rid=int(rid), mailbox=mailbox)
    await cq.message.answer(
        "🕐 Пришлите время(я) обновления через пробел/запятую в формате ЧЧ:ММ "
        "(по часам сервера, на проде UTC).\nСейчас: <b>" + ", ".join(cfg.get("times") or []) + "</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(
            text="⬅️ Отмена", callback_data=f"uniq:o:{code}:{service}:{rid}:{mailbox}")]]))
    await cq.answer()


@router.message(UniqTimes.value, F.text)
async def uniq_time_set(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.services import uniq
    data = await state.get_data()
    await state.clear()
    code = data.get("code", ""); service = data.get("service"); rid = data.get("rid"); mailbox = data.get("mailbox", "")
    try:
        times = uniq.parse_times(m.text or "")
    except ValueError as e:
        await m.answer(f"🔴 {e}\nПример: <code>08:00 13:00 19:00</code>")
        return
    cfg = _uniq_cfg_load(service, rid, mailbox)
    cfg["times"] = times
    cfg["mode"] = "schedule"
    _uniq_save(service, rid, mailbox, cfg)
    text, kb = _uniq_view(code, service, int(rid), mailbox)
    await m.answer("✅ Время обновления: <b>" + ", ".join(times) + "</b>\n\n" + text, reply_markup=kb)


@router.callback_query(F.data.startswith("uniq:gen:"))
async def uniq_gen(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    from bot.services import uniq
    _, _, code, service, rid, mailbox = cq.data.split(":", 5)
    cfg = _uniq_cfg_load(service, rid, mailbox)
    # генерим в строку текущего уровня редактирования; если здесь уникализация ещё не
    # сохранена (наследуется из ALL) — сохраняем сюда, чтобы было куда писать вариант
    _uniq_save(service, rid, mailbox, cfg)
    await cq.answer("⏳ Генерирую…")
    st, info = await uniq.generate(service, int(rid) or None, mailbox)
    note = {
        "ok": "✅ Вариант сгенерирован.",
        "okall": f"✅ Запущено. Рубрики ({info}) обновятся фоном в течение ~минуты на рубрику (лимит API).",
        "off": "⚠️ Сначала включите уникализацию и отметьте опции.",
        "wait": f"⏳ Лимит htmlmix: подождите {info} с и повторите.",
        "nosrc": "🔴 Нет исходного шаблона для этой связки.",
        "apierr": "🔴 htmlmix не вернул вариант (проверьте токен/квоту).",
    }.get(st, f"🔴 {info}")
    await _uniq_show(cq, code, service, rid, mailbox, note=note)


@router.callback_query(F.data.startswith("uniq:back:"))
async def uniq_back(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.clear()
    _, _, code, service, rid, mailbox = cq.data.split(":", 5)
    text, kb = _svc_cfg_view(code, service, int(rid), mailbox)
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()



# ═══════════ 🧩 Сервисы: добавление/удаление стран и сервисов (п.61) ═══════════
def _kb_back(cb: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Отмена", callback_data=cb)]])


def _split(text: str) -> list[str]:
    return [x for x in re.split(r"[\s,;]+", text or "") if x]


@router.callback_query(F.data == "svc:cadd")
async def svc_country_add_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await state.set_state(CatalogEdit.country)
    await cq.message.answer(
        "➕ Пришлите <b>ISO-код страны</b> (2 латинские буквы), можно несколько через пробел:\n"
        "<code>PL</code>  или  <code>LU SI AT</code>", reply_markup=_kb_back("svc:root"))
    await cq.answer()


@router.message(CatalogEdit.country, F.text)
async def svc_country_add(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    lines = []
    for raw in _split(m.text):
        code = catalog.norm_code(raw)
        if not code:
            lines.append(f"❌ {_esc(raw)} — нужен 2-буквенный ISO-код")
            continue
        ok, msg = catalog.add_country(code)
        lines.append(("✅ " if ok else "• ") + msg)
    await state.clear()
    await m.answer("\n".join(lines) or "Пусто.")
    await m.answer(_COUNTRIES_TXT, reply_markup=_kb_countries())


@router.callback_query(F.data.startswith("svc:cdel:"))
async def svc_country_del_ask(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    code = cq.data.split(":")[-1]
    n = len(catalog.services(code))
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"svc:cdelok:{code}")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"svc:c:{code}")],
    ])
    await cq.message.edit_text(
        f"Удалить страну <b>{code}</b> и её сервисы ({n})?\n"
        "<i>Настройки областей (базы SMTP, шаблоны, сокра) остаются в БД — "
        "вернутся, если добавить страну/сервис снова.</i>", reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("svc:cdelok:"))
async def svc_country_del(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    code = cq.data.split(":")[-1]
    n = catalog.delete_country(code)
    await cq.answer(f"{code} удалена (сервисов: {n})")
    await cq.message.edit_text(_COUNTRIES_TXT, reply_markup=_kb_countries())


@router.callback_query(F.data.startswith("svc:sadd:"))
async def svc_services_add_ask(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    code = cq.data.split(":")[-1]
    await state.update_data(cat_code=code)
    await state.set_state(CatalogEdit.services)
    await cq.message.answer(
        f"➕ Сервисы для <b>{code}</b> — по одному в строке или через пробел, например:\n"
        "<code>olx.pl</code>\n<code>allegro.pl</code>\n"
        f"Только латиница/цифры/точка/дефис, до {catalog.SERVICE_MAX_LEN} символов.",
        reply_markup=_kb_back(f"svc:c:{code}"))
    await cq.answer()


@router.message(CatalogEdit.services, F.text)
async def svc_services_add(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    code = (await state.get_data()).get("cat_code", "")
    added, dup, bad = catalog.add_services(code, _split(m.text))
    await state.clear()
    out = [f"✅ Добавлено: <b>{len(added)}</b>"]
    if dup:
        out.append("• Уже были: " + ", ".join(_esc(x) for x in dup))
    if bad:
        out.append("❌ Не подходят: " + ", ".join(f"<code>{_esc(x)}</code>" for x in bad[:10]))
    await m.answer("\n".join(out))
    text, kb = _country_view(code)
    await m.answer(text, reply_markup=kb)


@router.callback_query(F.data.startswith("svc:sdel:"))
async def svc_service_del_ask(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service = cq.data.split(":", 3)
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🗑 Да, удалить", callback_data=f"svc:sdelok:{code}:{service}")],
        [InlineKeyboardButton(text="⬅️ Отмена", callback_data=f"svc:s:{code}:{service}")],
    ])
    await cq.message.edit_text(
        f"Удалить сервис <b>{_esc(service)}</b> ({code})?\n"
        "<i>Настройки области/шаблоны этого сервиса остаются в БД — вернутся, если добавить его снова.</i>",
        reply_markup=kb)
    await cq.answer()


@router.callback_query(F.data.startswith("svc:sdelok:"))
async def svc_service_del(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    _, _, code, service = cq.data.split(":", 3)
    ok = catalog.delete_service(code, service)
    await cq.answer("Удалён" if ok else "Уже удалён")
    text, kb = _country_view(code)
    await cq.message.edit_text(text, reply_markup=kb)
