import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, Document
from aiogram.fsm.context import FSMContext
from sqlmodel import Session, select
from sqlalchemy import func
from common.db import get_engine
from common.models.smtp import SmtpAccount
from bot.services.smtp_pool import set_binding, get_binding
try:
    from common.models.send_log import SendLog  # noqa
except Exception:
    SendLog = None  # type: ignore
from common.security import encrypt_password
from bot.fsm.admin import SmtpAdd, SmtpAutoAdd
from common.config import settings
from bot.keyboards.admin_menu import admin_menu
from common import catalog  # страны/сервисы — каталог в БД (п.61)
from typing import List, Tuple
from typing import Optional
from bot.services.mailer import probe_smtp
import datetime as _dt
import re as _re
from html import escape as _html_escape

router = Router()
log = logging.getLogger("bot")

_engine = get_engine()

def kb_smtp_root() -> InlineKeyboardMarkup:
    # Тексты как ты задавал:
    # Add, Clear, Settings, Auto, Add auto, Clear auto, Forced change, Limit, Back
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="➕ Add", callback_data="smtp:add"),
            InlineKeyboardButton(text="🧹 Clear", callback_data="smtp:clear"),
        ],
        [
            InlineKeyboardButton(text="⚙️ Settings", callback_data="smtp:settings"),
            InlineKeyboardButton(text="🔁 Auto/Лимиты", callback_data="slim:root"),
        ],
        [
            InlineKeyboardButton(text="📥 Add auto", callback_data="smtp:auto_add"),
            InlineKeyboardButton(text="🗑 Clear auto", callback_data="smtp:auto_clear"),
        ],
        [
            InlineKeyboardButton(text="⏭ Forced change", callback_data="slim:forced"),
            InlineKeyboardButton(text="📈 Limit", callback_data="slim:root"),
        ],
        [
            InlineKeyboardButton(text="⬅️ Назад", callback_data="smtp:back"),
        ],
    ])


def _kb_add_cancel() -> InlineKeyboardMarkup:
    # Инлайн-кнопка отмены ввода SMTP (возврат в корень SMTP + сброс FSM)
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="smtp:add_cancel")],
    ])


def kb_smtp_clear() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🧽 Выборочно", callback_data="smtp:clear:pick"),
            InlineKeyboardButton(text="🗑 Неиспользуемые", callback_data="smtp:clear:unused"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="smtp:root")],
    ])

# --- Keyboard for pick-list for selective SMTP deletion ---
def _kb_clear_pick() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    stats = _fetch_smtp_stats()
    if not stats:
        rows.append([InlineKeyboardButton(text="Нет активных SMTP", callback_data="smtp:clear")])
    else:
        for sid, email, cnt in stats:
            txt = f"🗑 {_escape_html(email)} — {cnt}"
            rows.append([InlineKeyboardButton(text=txt, callback_data=f"smtp:clear:del:{sid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="smtp:clear")])
    rows.append([InlineKeyboardButton(text="🏠 В корень SMTP", callback_data="smtp:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- Keyboard for confirmation of unused SMTP deletion ---
def _kb_confirm_unused() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Да, удалить", callback_data="smtp:clear:unused:yes"),
            InlineKeyboardButton(text="❌ Нет", callback_data="smtp:clear"),
        ],
        [InlineKeyboardButton(text="🏠 В корень SMTP", callback_data="smtp:root")],
    ])


def kb_group_root(prefix: str) -> InlineKeyboardMarkup:
    # prefix: settings / auto / auto_clear / forced / limit
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="📮 Gmail", callback_data=f"smtp:{prefix}:gmail"),
            InlineKeyboardButton(text="📦 Main", callback_data=f"smtp:{prefix}:main"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="smtp:root")],
    ])


def kb_scope(prefix: str, group: str) -> InlineKeyboardMarkup:
    # Scope for chosen group (gmail/main): Global or Countries
    return InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="🌍 Global", callback_data=f"smtp:{prefix}:{group}:global"),
            InlineKeyboardButton(text="🗺 Страны", callback_data=f"smtp:{prefix}:{group}:countries"),
        ],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"smtp:{prefix}")],
    ])


def _fetch_smtp_stats() -> List[Tuple[int, str, int]]:
    rows: List[Tuple[int, str, int]] = []
    with Session(_engine) as db:
        accounts = db.exec(
            select(SmtpAccount.id, SmtpAccount.email)
            .where(SmtpAccount.is_active == True)  # noqa: E712
            .order_by(SmtpAccount.id)
        ).all()
        if not accounts:
            return rows
        # Build maps for counts depending on SendLog schema
        counts_by_id: dict[int, int] = {}
        counts_by_email: dict[str, int] = {}
        if SendLog is not None:
            try:
                # Prefer smtp_id if present
                if hasattr(SendLog, 'smtp_id'):
                    for sid, cnt in db.exec(
                        select(getattr(SendLog, 'smtp_id'), func.count())
                        .group_by(getattr(SendLog, 'smtp_id'))
                    ).all():
                        if sid is not None:
                            counts_by_id[int(sid)] = int(cnt)
                # Also try smtp_email if present (to be robust)
                if hasattr(SendLog, 'smtp_email'):
                    for semail, cnt in db.exec(
                        select(getattr(SendLog, 'smtp_email'), func.count())
                        .group_by(getattr(SendLog, 'smtp_email'))
                    ).all():
                        if semail:
                            counts_by_email[str(semail)] = int(cnt)
            except Exception:
                # ignore counting errors, render zeros below
                pass
        # Merge counts into ordered rows
        for sid, email in accounts:
            cnt = counts_by_id.get(int(sid), counts_by_email.get(str(email), 0))
            rows.append((int(sid), str(email), int(cnt)))
    return rows


def _render_smtp_list(title: str) -> str:
    stats = _fetch_smtp_stats()
    if not stats:
        return f"{title}\n\nПока нет активных SMTP."
    return f"{title}\n\nВыберите, какой SMTP закрепить:"  # список будет в инлайн-кнопках


# --- Inline keyboard for picking SMTP in global scope ---
def _kb_pick_global(group: str, current_id: int | None) -> InlineKeyboardMarkup:
    items = _fetch_smtp_stats()
    rows: List[List[InlineKeyboardButton]] = []
    for sid, email, cnt in items:
        mark = "✅ " if (current_id is not None and sid == current_id) else ""
        txt = f"{mark}{_escape_html(email)} — {cnt}"
        rows.append([InlineKeyboardButton(text=txt, callback_data=f"smtp:set:{group}:global:{sid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"smtp:settings:{group}")])
    rows.append([InlineKeyboardButton(text="🏠 В корень SMTP", callback_data="smtp:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

# --- Inline keyboard for picking country and country-scoped SMTP ---
def _chunk(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i+n]

def _kb_countries(group: str) -> InlineKeyboardMarkup:
    # Show ALL countries at once, 3 per row, no pagination
    rows: List[List[InlineKeyboardButton]] = []
    for chunk in _chunk(catalog.countries(), 3):
        btns = [
            InlineKeyboardButton(
                text=f"{c.flag} {c.code}",
                callback_data=f"smtp:country:{group}:{c.code}"
            )
            for c in chunk
        ]
        rows.append(btns)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"smtp:settings:{group}")])
    rows.append([InlineKeyboardButton(text="🏠 В корень SMTP", callback_data="smtp:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_services_for_country(group: str, country_code: str) -> InlineKeyboardMarkup:
    services = catalog.services(country_code)
    rows: List[List[InlineKeyboardButton]] = []
    # First row: bind whole country
    rows.append([
        InlineKeyboardButton(text="🌐 Вся страна", callback_data=f"smtp:countrybind:{group}:{country_code}")
    ])
    # Services: 3 per row
    for chunk in _chunk(services, 3):
        rows.append([
            InlineKeyboardButton(
                text=str(svc),
                callback_data=f"smtp:service:{group}:{country_code}:{svc}"
            ) for svc in chunk
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"smtp:settings:{group}:countries")])
    rows.append([InlineKeyboardButton(text="🏠 В корень SMTP", callback_data="smtp:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_pick_service(group: str, country_code: str, service: str, current_id: Optional[int]) -> InlineKeyboardMarkup:
    items = _fetch_smtp_stats()
    rows: List[List[InlineKeyboardButton]] = []
    for sid, email, cnt in items:
        mark = "✅ " if (current_id is not None and sid == current_id) else ""
        txt = f"{mark}{_escape_html(email)} — {cnt}"
        rows.append([
            InlineKeyboardButton(
                text=txt,
                callback_data=f"smtp:set:{group}:service:{country_code}:{service}:{sid}"
            )
        ])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"smtp:country:{group}:{country_code}")])
    rows.append([InlineKeyboardButton(text="🏠 В корень SMTP", callback_data="smtp:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _kb_pick_country(group: str, country_code: str, current_id: Optional[int]) -> InlineKeyboardMarkup:
    items = _fetch_smtp_stats()
    rows: List[List[InlineKeyboardButton]] = []
    for sid, email, cnt in items:
        mark = "✅ " if (current_id is not None and sid == current_id) else ""
        txt = f"{mark}{_escape_html(email)} — {cnt}"
        rows.append([InlineKeyboardButton(text=txt, callback_data=f"smtp:set:{group}:country:{country_code}:{sid}")])
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data=f"smtp:country:{group}:{country_code}")])
    rows.append([InlineKeyboardButton(text="🏠 В корень SMTP", callback_data="smtp:root")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

async def show_smtp_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("SMTP — выберите действие:", reply_markup=kb_smtp_root())

@router.callback_query(F.data.startswith("smtp:") & ~F.data.startswith("smtp:country:") & ~F.data.startswith("smtp:service:") & ~F.data.startswith("smtp:countrybind:") & ~F.data.startswith("smtp:set:") & ~F.data.startswith("smtp:clear:"))
async def smtp_nav(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return

    action = cq.data.split(":", 1)[1]

    if action == "add":
        await cq.message.answer(
            "Введите SMTP в формате:\n"
            "<code>host:port:user:email:pass</code>\n\n"
            "Либо пришлите <b>.txt</b> файл (каждая строка — одна запись).",
            reply_markup=_kb_add_cancel(),
        )
        await state.set_state(SmtpAdd.waiting_input)
        await cq.answer()
        return

    if action == "auto_add":
        await cq.message.answer(
            "Загрузка в автопул SMTP.\n\n"
            "Введите строки в формате:\n"
            "<code>host:port:user:email:pass</code>\n\n"
            "Либо пришлите <b>.txt</b> файл (каждая строка — одна запись).",
            reply_markup=_kb_add_cancel(),
        )
        await state.set_state(SmtpAutoAdd.waiting_input)
        await cq.answer()
        return

    if action == "add_cancel":
        # Отмена ввода SMTP: чистим FSM и возвращаемся в корень SMTP
        await state.clear()
        try:
            await cq.message.edit_text("SMTP — выберите действие:", reply_markup=kb_smtp_root())
        except Exception:
            await cq.message.answer("SMTP — выберите действие:", reply_markup=kb_smtp_root())
        await cq.answer("Отменено")
        return

    if action == "back":
        # Возврат из SMTP в админку: удаляем инлайн-меню, показываем reply-меню одним сообщением
        from bot.handlers.admin.index import back_to_admin
        await back_to_admin(cq)
        return

    if action == "root":
        # Возврат в корень раздела SMTP (остаёмся внутри инлайн-меню)
        try:
            await cq.message.edit_text("SMTP — выберите действие:", reply_markup=kb_smtp_root())
        except Exception:
            await cq.message.answer("SMTP — выберите действие:", reply_markup=kb_smtp_root())
        await cq.answer()
        return

    # Подменю: Clear
    if action == "clear":
        await cq.message.edit_text("SMTP → Очистка", reply_markup=kb_smtp_clear())
        await cq.answer()
        return

    # Settings / Auto / Clear auto / Forced change / Limit → выбор группы (gmail/main)
    if action in {"settings", "auto", "auto_clear", "forced", "limit"}:
        title = {
            "settings": "Настройки",
            "auto": "Автосмена",
            "auto_clear": "Очистка автопула",
            "forced": "Принудительная смена",
            "limit": "Лимит",
        }[action]
        await cq.message.edit_text(f"SMTP → {title} → выберите группу:", reply_markup=kb_group_root(action))
        await cq.answer()
        return

    # Выбор группы для settings/auto/auto_clear/forced/limit
    for prefix in ("settings", "auto", "auto_clear", "forced", "limit"):
        if action.startswith(prefix + ":"):
            tail = action[len(prefix)+1:]  # e.g. gmail / main / gmail:global / gmail:countries
            if tail in {"gmail", "main"}:
                await cq.message.edit_text(
                    f"SMTP → {prefix} → {tail} → выберите область:",
                    reply_markup=kb_scope(prefix, tail)
                )
                await cq.answer()
                return
            # Ветки области (global/countries)
            if ":" in tail:
                group, sub = tail.split(":", 1)
                pretty = {
                    "settings": "Настройки",
                    "auto": "Автосмена",
                    "auto_clear": "Очистка автопула",
                    "forced": "Принудительная смена",
                    "limit": "Лимит",
                }[prefix]
                if sub == 'global' and prefix == 'settings':
                    title = f"SMTP → Настройки → {group} → Global"
                    text = _render_smtp_list(title)
                    try:
                        current_id = await get_binding(group=group, scope="global")
                    except Exception:
                        current_id = None
                    await cq.message.edit_text(
                        text,
                        reply_markup=_kb_pick_global(group, current_id)
                    )
                    await cq.answer()
                    return
                elif sub == 'countries' and prefix == 'settings':
                    await cq.message.edit_text(
                        f"SMTP → Настройки → {group} → Страны",
                        reply_markup=_kb_countries(group)
                    )
                    await cq.answer()
                    return
                # default placeholder for other scopes
                await cq.message.edit_text(
                    f"SMTP → {pretty} → {group} → {sub}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="⬅️ Назад", callback_data=f"smtp:{prefix}:{group}")],
                        [InlineKeyboardButton(text="🏠 В корень SMTP", callback_data="smtp:root")],
                    ])
                )
                await cq.answer()
                return

# (REMOVED handler for paginated countries list)

# --- Callback handler for picking a country (now shows services) ---
@router.callback_query(F.data.startswith("smtp:country:"))
async def smtp_country_pick(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    # format: smtp:country:{group}:{code}
    try:
        _, _, group, code = cq.data.split(":", 3)
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return
    await cq.message.edit_text(
        f"SMTP → Настройки → {group} → {code} → сервисы",
        reply_markup=_kb_services_for_country(group, code)
    )
    await cq.answer()

# --- Handler for binding whole country ---
@router.callback_query(F.data.startswith("smtp:countrybind:"))
async def smtp_country_bind(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    # format: smtp:countrybind:{group}:{code}
    try:
        _, _, group, code = cq.data.split(":", 3)
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return
    scope = f"country:{code}"
    try:
        current_id = await get_binding(group=group, scope=scope)
    except Exception:
        current_id = None
    await cq.message.edit_text(
        f"SMTP → Настройки → {group} → {code} (вся страна)",
        reply_markup=_kb_pick_country(group, code, current_id)
    )
    await cq.answer()

# --- Handler for picking a service ---
@router.callback_query(F.data.startswith("smtp:service:"))
async def smtp_service_pick(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    # format: smtp:service:{group}:{code}:{service}
    try:
        _, _, group, code, service = cq.data.split(":", 4)
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return
    scope = f"service:{code}:{service}"
    try:
        current_id = await get_binding(group=group, scope=scope)
    except Exception:
        current_id = None
    await cq.message.edit_text(
        f"SMTP → Настройки → {group} → {code} → {service}",
        reply_markup=_kb_pick_service(group, code, service, current_id)
    )
    await cq.answer()


# --- Handler for selective pick-list deletion ---
@router.callback_query(F.data == "smtp:clear:pick")
async def smtp_clear_pick(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await cq.message.edit_text(
        "SMTP → Очистка → Выборочно\n\nНажмите на SMTP для удаления:",
        reply_markup=_kb_clear_pick()
    )
    await cq.answer()

# --- Handler for unused SMTP deletion confirmation ---
@router.callback_query(F.data == "smtp:clear:unused")
async def smtp_clear_unused(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return

    # Под неиспользуемыми считаем те, что не привязаны ни к одной области (global/country/service) и с 0 отправок
    with Session(_engine) as db:
        # Счётчики отправок по smtp_id (если модель поддерживает)
        send_counts = {}
        if SendLog is not None and hasattr(SendLog, 'smtp_id'):
            for sid, cnt in db.exec(
                select(getattr(SendLog, 'smtp_id'), func.count()).group_by(getattr(SendLog, 'smtp_id'))
            ).all():
                if sid is not None:
                    send_counts[int(sid)] = int(cnt)

        # Импорт из моделей — это именно таблица привязок
        from common.models.autoswitch import SmtpBinding  # type: ignore

        # Получаем id привязанных
        bound_ids = set()
        if SmtpBinding is not None:
            for sid, in db.exec(select(SmtpBinding.smtp_id).where(SmtpBinding.smtp_id.is_not(None))).all():
                bound_ids.add(int(sid))
        # SMTP из баз областей (SRVS → Сервисы) тоже считаются используемыми
        from common.models.scope import ScopeSmtp
        bound_ids.update(int(x) for x in db.exec(select(ScopeSmtp.smtp_id)).all())

        # Кандидаты на удаление: активные, но без привязки и без отправок
        candidates = []
        for sid, email, _ in _fetch_smtp_stats():
            if sid not in bound_ids and send_counts.get(sid, 0) == 0:
                candidates.append((sid, email))

        # Если вообще нет активных SMTP — нечего удалять
        if not _fetch_smtp_stats():
            await cq.answer("Активных SMTP нет", show_alert=True)
            return

        if not candidates:
            await cq.answer("Нет неиспользуемых SMTP", show_alert=True)
            return

        # Сохраняем список id в caption (простой способ передать состояние без FSM)
        ids_str = ",".join(str(sid) for sid, _ in candidates)
        await cq.message.edit_text(
            "SMTP → Очистка → Неиспользуемые\n\n"
            f"Найдено: <b>{len(candidates)}</b>\n"
            "Удалить все?\n\n"
            "<i>Под неиспользуемыми считаем: не привязаны и без отправок.</i>\n"
            f"<code>ids:{ids_str}</code>",
            reply_markup=_kb_confirm_unused()
        )
        await cq.answer()

# --- Handler to execute confirmed deletion of unused SMTP ---
@router.callback_query(F.data == "smtp:clear:unused:yes")
async def smtp_clear_unused_yes(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    # Достаём ids из текущего текста сообщения (строка вида code>ids:1,2,3</code>)
    text = cq.message.text or ""
    ids = []
    m = _re.search(r"ids:([0-9,]+)", text)
    if m:
        ids = [int(x) for x in m.group(1).split(",") if x.isdigit()]

    removed = 0
    with Session(_engine) as db:
        for sid in ids:
            obj = db.get(SmtpAccount, sid)
            if obj:
                db.delete(obj)
                removed += 1
        db.commit()

    await cq.message.edit_text(
        f"✅ Удалено: <b>{removed}</b>",
        reply_markup=kb_smtp_clear()
    )
    await cq.answer("Готово")

# --- Handler to delete a single SMTP from the pick-list ---
@router.callback_query(F.data.startswith("smtp:clear:del:"))
async def smtp_clear_delete_one(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    try:
        sid = int(cq.data.split(":")[-1])
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return

    with Session(_engine) as db:
        obj = db.get(SmtpAccount, sid)
        if not obj:
            await cq.answer("Уже удалён", show_alert=True)
        else:
            db.delete(obj)
            db.commit()
            await cq.answer("Удалён")

    # Перерисовать список
    await cq.message.edit_text(
        "SMTP → Очистка → Выборочно\n\nНажмите на SMTP для удаления:",
        reply_markup=_kb_clear_pick()
    )

@router.message(SmtpAdd.waiting_input, F.text)
async def smtp_add_text(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    lines = [m.text.strip()]
    ok, err, errors = await _ingest_lines(lines, pool="manual")
    await m.answer(_ingest_report(ok, err, errors, total=len(lines)))
    await state.clear()


@router.message(SmtpAdd.waiting_input, F.document)
async def smtp_add_file(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    doc: Document = m.document
    if not (doc.file_name and doc.file_name.lower().endswith(".txt")):
        await m.answer("Нужен .txt файл. Каждая строка: <code>host:port:user:email:pass</code>")
        return
    file = await m.bot.get_file(doc.file_id)
    content = await m.bot.download_file(file.file_path)
    text = content.read().decode("utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    ok, err, errors = await _ingest_lines(lines, pool="manual")
    await m.answer(_ingest_report(ok, err, errors, total=len(lines)))
    await state.clear()


@router.message(SmtpAutoAdd.waiting_input, F.text)
async def smtp_auto_add_text(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    lines = [m.text.strip()]
    ok, err, errors = await _ingest_lines(lines, pool="auto")
    await m.answer(_ingest_report(ok, err, errors, total=len(lines)))
    await state.clear()


@router.message(SmtpAutoAdd.waiting_input, F.document)
async def smtp_auto_add_file(m: Message, state: FSMContext):
    if m.from_user.id not in settings.admin_ids_set:
        return
    doc: Document = m.document
    if not (doc.file_name and doc.file_name.lower().endswith(".txt")):
        await m.answer("Нужен .txt файл. Каждая строка: <code>host:port:user:email:pass</code>")
        return
    file = await m.bot.get_file(doc.file_id)
    content = await m.bot.download_file(file.file_path)
    text = content.read().decode("utf-8", errors="ignore")
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    ok, err, errors = await _ingest_lines(lines, pool="auto")
    await m.answer(_ingest_report(ok, err, errors, total=len(lines)))
    await state.clear()


async def _ingest_lines(lines: List[str], pool: str) -> Tuple[int, int, List[str]]:
    ok = 0
    err = 0
    errors: List[str] = []
    with Session(_engine) as db:
        for ln in lines:
            try:
                host, port, user, email, password = _parse_smtp_line_flexible(ln)
                # probe connectivity + test send
                probe = await probe_smtp(
                    host=host,
                    port=port,
                    login=user,
                    sender_email=email,
                    password=password,
                )
                if not probe.ok:
                    details = probe.error or probe.smtp_response or "probe failed"
                    errors.append(f"{ln} -> {details}")
                    log.warning("probe failed for %s:%s %s -> %s", host, port, email, details)
                    err += 1
                    continue

                rec = SmtpAccount(
                    kind=_infer_kind(email),
                    country=None,
                    host=host,
                    port=port,
                    login=user,
                    email=email,
                    password_enc=encrypt_password(password),
                    is_active=True,
                    error_count=0,
                    pool=pool,
                )
                # optional fields if present in model
                if hasattr(rec, "is_verified"):
                    setattr(rec, "is_verified", True)
                if hasattr(rec, "last_check_at"):
                    setattr(rec, "last_check_at", _dt.datetime.utcnow())
                if hasattr(rec, "check_error"):
                    setattr(rec, "check_error", None)

                db.add(rec)
                ok += 1
            except Exception as e:
                errors.append(f"{ln} -> {e}")
                log.warning("bad smtp line: %s (%s)", ln, e)
                err += 1
        db.commit()
    return ok, err, errors


_DELIMS = [":", ";", ",", "|", "\t", " "]


def _parse_smtp_line_flexible(ln: str) -> Tuple[str, int, str, str, str]:
    s = ln.strip()
    if not s:
        raise ValueError("empty line")

    # 1) Try pattern: user:pass@host:port email
    m = _re.match(r"^(?P<user>[^:@\s]+):(?P<pw>[^@\s]+)@(?P<host>[^:\s]+):(?P<port>\d+)\s+(?P<email>\S+)$", s)
    if m:
        return (
            m.group("host").strip(),
            int(m.group("port")),
            m.group("user").strip(),
            m.group("email").strip(),
            m.group("pw").strip(),
        )

    # 2) Try common delimited sequences host,port,user,email,pass (any delim)
    for d in _DELIMS:
        parts = [p for p in s.split(d) if p != ""]
        if len(parts) >= 5:
            # Heuristic: detect which pos is email (contains '@'), which is port (int)
            try:
                # prefer host:port:user:email:pass order
                host = parts[0].strip()
                port = int(parts[1].strip())
                user = parts[2].strip()
                email = parts[3].strip()
                pw = d.join(parts[4:]).strip()  # allow delim inside password
                if "@" not in email:
                    raise ValueError("email missing @")
                return host, port, user, email, pw
            except Exception:
                # fallback: any order with email and port detectable
                email_idx = next((i for i, p in enumerate(parts) if "@" in p), None)
                port_idx = next((i for i, p in enumerate(parts) if p.isdigit()), None)
                if email_idx is not None and port_idx is not None:
                    email = parts[email_idx].strip()
                    port = int(parts[port_idx].strip())
                    # remove those to guess host/user/pw positions
                    rest = [p.strip() for i, p in enumerate(parts) if i not in (email_idx, port_idx)]
                    if len(rest) < 3:
                        continue
                    host = rest[0]
                    user = rest[1]
                    pw = d.join(rest[2:]).strip()
                    return host, port, user, email, pw
    raise ValueError("Unsupported SMTP format: expected host:port:user:email:pass or user:pass@host:port email")


def _infer_kind(email: str) -> str:
    return "gmail" if email.lower().endswith("@gmail.com") else "main"

def _escape_html(s: str) -> str:
    return _html_escape(s, quote=True)

def _ingest_report(ok: int, err: int, errors: List[str], total: int) -> str:
    # Если одна строка — показываем краткий статус с цитатой ошибки
    if total == 1:
        if ok == 1:
            return "✅ <b>Добавлено</b>"
        # показать первую ошибку красиво
        first = errors[0] if errors else "Неизвестная ошибка"
        return "❌ <b>Не добавлено</b>\n\n<blockquote><code>" + _escape_html(first) + "</code></blockquote>"

    # Для батча — сводка и первые несколько ошибок
    text = [
        "Готово.",
        f"Добавлено: <b>{ok}</b>",
        f"Ошибок: <b>{err}</b>",
    ]
    if err:
        text.append("")
        max_show = 5
        for i, e in enumerate(errors[:max_show], 1):
            text.append(f"<blockquote><code>{_escape_html(e)}</code></blockquote>")
        if err > max_show:
            text.append(f"… и ещё {err - max_show} ошибок")
    return "\n".join(text)
@router.callback_query(F.data.startswith("smtp:set:"))
async def smtp_set_binding(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    # format:
    #  - smtp:set:{group}:global:{smtp_id}
    #  - smtp:set:{group}:country:{CODE}:{smtp_id}
    parts = cq.data.split(":")
    try:
        # parts[0]=smtp, parts[1]=set, parts[2]=group
        group = parts[2]
        if parts[3] == "global":
            scope = "global"
            smtp_id = int(parts[4])
        elif parts[3] == "country":
            scope = f"country:{parts[4]}"
            smtp_id = int(parts[5])
        elif parts[3] == "service":
            # smtp:set:{group}:service:{CODE}:{service}:{smtp_id}
            scope = f"service:{parts[4]}:{parts[5]}"
            smtp_id = int(parts[6])
        else:
            raise ValueError("bad scope")
    except Exception:
        await cq.answer("Неверные данные", show_alert=True)
        return

    try:
        await set_binding(group=group, scope=scope, smtp_id=smtp_id)
    except Exception as e:
        await cq.answer(f"Ошибка: {e}", show_alert=True)
        return

    # re-render after set
    if scope == "global":
        title = f"SMTP → Настройки → {group} → Global"
        text = _render_smtp_list(title)
        await cq.message.edit_text(text, reply_markup=_kb_pick_global(group, smtp_id))
        await cq.answer("Закреплено")
    elif scope.startswith("country:"):
        code = scope.split(":", 1)[1]
        await cq.message.edit_text(
            f"SMTP → Настройки → {group} → {code}",
            reply_markup=_kb_pick_country(group, code, smtp_id)
        )
        await cq.answer("Закреплено")
    elif scope.startswith("service:"):
        _, rest = scope.split(":", 1)
        code, service = rest.split(":", 1)
        await cq.message.edit_text(
            f"SMTP → Настройки → {group} → {code} → {service}",
            reply_markup=_kb_pick_service(group, code, service, smtp_id)
        )
        await cq.answer("Закреплено")