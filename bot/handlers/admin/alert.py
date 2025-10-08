from aiogram.types import Message
from common.config import settings

async def show_alert_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("ALERT — настройки тревог/уведомлений (подключим далее).")