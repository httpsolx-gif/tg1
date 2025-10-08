import logging
from aiogram import Router, F
from aiogram.types import Message
from common.config import settings
from bot.keyboards.admin_menu import admin_menu
from bot.keyboards.main_menu import main_menu

router = Router()
log = logging.getLogger("bot")

# вход в админку (с главного меню)
@router.message(F.text == "🛠 Админка")
async def admin_entry(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        await m.answer("⛔️ Нет доступа.")
        return
    await m.answer("Админка:", reply_markup=admin_menu())

# выход из админки назад в главное
@router.message(F.text == "⬅️ Назад")
async def admin_back(m: Message):
    is_admin = (m.from_user.id in settings.admin_ids_set)
    await m.answer("Главное меню:", reply_markup=main_menu(is_admin=is_admin))

# --- РАЗДЕЛЫ ---

@router.message(F.text == "📧 SMTP")
async def admin_smtp(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    # Покажем экран SMTP из отдельного модуля
    from bot.handlers.admin.smtp import show_smtp_root
    await show_smtp_root(m)

@router.message(F.text == "🔗 SHORT")
async def admin_short(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.keyboards.short_menu import kb_short_root
    await m.answer("🔗 Short menu", reply_markup=kb_short_root())

@router.message(F.text == "🧩 SRVS")
async def admin_srvs(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.srvs import show_srvs_root
    await show_srvs_root(m)

@router.message(F.text == "📊 STATS")
async def admin_stats(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.stats import show_stats_root
    await show_stats_root(m)

@router.message(F.text == "👥 USERS")
async def admin_users(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.users import show_users_root
    await show_users_root(m)

@router.message(F.text == "🛡 API")
async def admin_api(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.api import show_api_root
    await show_api_root(m)

@router.message(F.text == "🧪 TEST")
async def admin_test(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.test import show_test_root
    await show_test_root(m)

@router.message(F.text == "🚨 ALERT")
async def admin_alert(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    from bot.handlers.admin.alert import show_alert_root
    await show_alert_root(m)