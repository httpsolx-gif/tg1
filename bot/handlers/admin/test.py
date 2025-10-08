from aiogram.types import Message
from common.config import settings

async def show_test_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("TEST — короткие проверки short/smtp/ноды (подключим далее).")