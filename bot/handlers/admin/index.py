import logging
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.exceptions import TelegramBadRequest
from common.config import settings
from bot.keyboards.admin_menu import admin_menu, admin_menu_inline, ADMIN_BUTTONS
from bot.keyboards.main_menu import main_menu
from bot.fsm.admin import (
    SmtpAdd, SmtpAutoAdd, NodeAdd, NodeEdit, TemplateUpload, RubricAdd, RubricEdit,
    RubricMailboxAdd, MailboxAdd, MailboxDomains, ScopeInput, SmtpLimit,
    ServiceCfgEdit, ShortDomainAdd, AccessAdd, UserFlow, ApiTokenFlow, SpamSeedAdd,
    BroadcastFlow, CatalogEdit,
)

router = Router()
log = logging.getLogger("bot")

# Тексты кнопок нижнего меню — их нажатие в режиме ввода (SMTP/ноды) означает «выйти».
from bot.keyboards.icons import strip_lead_emoji as _strip
MENU_TEXTS = {"⬅️ Назад", "🛠 Админка", "📤 Отправить письмо", "📋 Меню"}
MENU_TEXTS |= {txt for row in ADMIN_BUTTONS for txt in row}
MENU_TEXTS |= {_strip(t) for t in set(MENU_TEXTS)}  # варианты без эмодзи (iconize reply-меню)

# вход в админку (с главного меню)
@router.message(F.text.func(lambda t: isinstance(t, str) and _strip(t) == "Админка"))
async def admin_entry(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        await m.answer("⛔️ Нет доступа.")
        return
    # Инлайн-меню: на нём рендерятся кастом-иконки (на reply-кнопках — нет).
    await m.answer("🛠 Админка — выберите раздел:", reply_markup=admin_menu_inline())

# Если пользователь в режиме ввода SMTP жмёт нижнее меню (Назад/разделы) —
# отменяем ввод и сбрасываем FSM, иначе нажатие «съедается» как SMTP-строка.
@router.message(
    StateFilter(
        SmtpAdd.waiting_input, SmtpAutoAdd.waiting_input,
        NodeAdd.waiting_input, NodeAdd.waiting_region, NodeEdit.region,
        TemplateUpload.waiting, RubricAdd.name, RubricEdit.value, RubricMailboxAdd.waiting, MailboxAdd.waiting,
        MailboxDomains.waiting, ServiceCfgEdit.value,
        ScopeInput.smtp, ScopeInput.limit,
        SmtpLimit.value,
        ShortDomainAdd.domain, ShortDomainAdd.token, ShortDomainAdd.email,
        ShortDomainAdd.registrar, ShortDomainAdd.node,
        AccessAdd.waiting, UserFlow.act, UserFlow.search,
        ApiTokenFlow.label, ApiTokenFlow.edit, ApiTokenFlow.supp,
        SpamSeedAdd.waiting,
        BroadcastFlow.targets, BroadcastFlow.caption, BroadcastFlow.photo,
        BroadcastFlow.interval, BroadcastFlow.cap, BroadcastFlow.delays,
        CatalogEdit.country, CatalogEdit.services,
    ),
    F.text.func(lambda t: isinstance(t, str) and _strip(t) in MENU_TEXTS),
)
async def cancel_add_on_menu(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Ввод отменён.", reply_markup=admin_menu())

# выход из админки назад в главное
@router.message(F.text.in_({"⬅️ Назад", "Назад"}))
async def admin_back(m: Message):
    is_admin = (m.from_user.id in settings.admin_ids_set)
    await m.answer("Главное меню:", reply_markup=main_menu(is_admin=is_admin))

# возврат в админку из инлайн-разделов (SHORT и т.п.) по кнопке «⬅️ Назад»
@router.callback_query(F.data == "admin:back")
async def admin_back_cb(cq: CallbackQuery):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    await back_to_admin(cq)


async def back_to_admin(cq: CallbackQuery) -> None:
    """Вернуться в корень админки (инлайн-меню с кастом-иконками), редактируя сообщение."""
    try:
        await cq.message.edit_text("🛠 Админка — выберите раздел:", reply_markup=admin_menu_inline())
    except TelegramBadRequest:
        await cq.message.answer("🛠 Админка — выберите раздел:", reply_markup=admin_menu_inline())
    await cq.answer()


# --- ИНЛАЙН-диспетчер разделов админки ---
@router.callback_query(F.data.startswith("adm:"))
async def admin_section(cq: CallbackQuery, state: FSMContext):
    if cq.from_user.id not in settings.admin_ids_set:
        await cq.answer("Нет доступа", show_alert=True)
        return
    sec = cq.data.split(":", 1)[1]
    await state.clear()

    if sec == "back":
        # из админки в главное меню (reply)
        try:
            await cq.message.delete()
        except TelegramBadRequest:
            pass
        await cq.message.answer("Главное меню:", reply_markup=main_menu(is_admin=True))
        await cq.answer()
        return

    # text + keyboard каждого раздела (переиспользуем сборщики клавиатур)
    from bot.handlers.admin.smtp import kb_smtp_root
    from bot.keyboards.short_menu import kb_short_root
    from bot.handlers.admin.nodes import kb_nodes_root
    from bot.handlers.admin.tpl import kb_templates_root
    from bot.handlers.admin.rubrics import kb_srvs_root
    from bot.handlers.admin.stats import kb_stats_root
    from bot.handlers.admin.backup import kb_backup_root
    from bot.handlers.admin.test import kb_test_root
    from bot.handlers.admin.users import kb_users_root
    from bot.handlers.admin.api import kb_api_root
    from bot.handlers.admin.spamcheck import kb_spamcheck_root
    from bot.handlers.admin.broadcast import kb_broadcast_root, _root_text as bc_root_text

    screens = {
        "smtp": ("SMTP — выберите действие:", kb_smtp_root),
        "short": ("🔗 Short menu", kb_short_root),
        "nodes": ("🖥 NODES — ноды отправки:", kb_nodes_root),
        "templ": ("📑 TEMPLATES — шаблоны писем:", kb_templates_root),
        "srvs": ("🧩 SRVS:", kb_srvs_root),
        "stats": ("📊 <b>STATS</b> — отправки / переходы / конверсия:", kb_stats_root),
        "backup": ("💾 <b>BACKUP</b> — резервные копии и выгрузка БД:", kb_backup_root),
        "test": ("🧪 <b>TEST</b> — проверки и удаление нерабочих:", kb_test_root),
        "users": ("👥 <b>USERS</b> — списки доступа отправителей:", kb_users_root),
        "api": ("🛡 <b>API</b> — токены публичного API:", kb_api_root),
        "spam": ("🕵️ <b>SPAM-check</b> — сид-ящики и проверка размещения:", kb_spamcheck_root),
        "bc": (bc_root_text(), kb_broadcast_root),
    }
    if sec in screens:
        title, kb_fn = screens[sec]
        try:
            await cq.message.edit_text(title, reply_markup=kb_fn())
        except TelegramBadRequest:
            await cq.message.answer(title, reply_markup=kb_fn())
        await cq.answer()
        return

    # заглушки — просто текст + кнопка назад в админку
    stubs = {
        "alert": "🚨 ALERT — настройки тревог/уведомлений (подключим далее).",
    }
    text = stubs.get(sec, "Раздел не найден.")
    from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")]])
    try:
        await cq.message.edit_text(text, reply_markup=kb)
    except TelegramBadRequest:
        await cq.message.answer(text, reply_markup=kb)
    await cq.answer()

# --- РАЗДЕЛЫ ---

@router.message(F.text.in_({"📧 SMTP", "SMTP"}))
async def admin_smtp(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    # Покажем экран SMTP из отдельного модуля
    from bot.handlers.admin.smtp import show_smtp_root
    await show_smtp_root(m)

@router.message(F.text.in_({"🔗 SHORT", "SHORT"}))
async def admin_short(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.keyboards.short_menu import kb_short_root
    await m.answer("🔗 Short menu", reply_markup=kb_short_root())

@router.message(F.text.in_({"🧩 SRVS", "SRVS"}))
async def admin_srvs(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.rubrics import show_rubrics_root
    await show_rubrics_root(m)

@router.message(F.text.in_({"📊 STATS", "STATS"}))
async def admin_stats(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.stats import show_stats_root
    await show_stats_root(m)

@router.message(F.text.in_({"💾 BACKUP", "BACKUP"}))
async def admin_backup(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.backup import show_backup_root
    await show_backup_root(m)

@router.message(F.text.in_({"👥 USERS", "USERS"}))
async def admin_users(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.users import show_users_root
    await show_users_root(m)

@router.message(F.text.in_({"🛡 API", "API"}))
async def admin_api(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.api import show_api_root
    await show_api_root(m)

@router.message(F.text.in_({"🧪 TEST", "TEST"}))
async def admin_test(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.test import show_test_root
    await show_test_root(m)

@router.message(F.text.in_({"🚨 ALERT", "ALERT"}))
async def admin_alert(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.alert import show_alert_root
    await show_alert_root(m)

@router.message(F.text.in_({"📣 BC", "BC", "BROADCAST"}))
async def admin_broadcast(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.broadcast import show_broadcast_root
    await show_broadcast_root(m)

@router.message(F.text.in_({"🕵️ SPAM", "SPAM"}))
async def admin_spam(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.spamcheck import show_spamcheck_root
    await show_spamcheck_root(m)

@router.message(F.text.in_({"🖥 NODES", "NODES"}))
async def admin_nodes(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.nodes import show_nodes_root
    await show_nodes_root(m)

@router.message(F.text.in_({"📑 TEMPL", "TEMPL"}))
async def admin_templates(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.tpl import show_templates_root
    await show_templates_root(m)

@router.message(F.text.in_({"📮 MAIL", "MAIL"}))
async def admin_mailtypes(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.mailtypes import show_mailtypes_root
    await show_mailtypes_root(m)
