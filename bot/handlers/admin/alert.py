"""ALERT — уведомления и рассылки. Внутри: 📣 авторассылка по группам (BC, userbot)."""
from aiogram.types import Message, InlineKeyboardMarkup, InlineKeyboardButton
from common.config import settings

ALERT_TITLE = "🚨 <b>ALERT</b> — уведомления и рассылки:"


def kb_alert_root() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📣 Авторассылка по группам", callback_data="bc:root")],  # BC (раньше — отдельный раздел)
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin:back")],
    ])


async def show_alert_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer(ALERT_TITLE, reply_markup=kb_alert_root())
