from aiogram.types import Message
from common.config import settings

async def show_srvs_root(m: Message):
    if m.from_user.id not in settings.admin_ids_set:
        return
    await m.answer("SRVS — добавление/удаление сервисов, sender/title (подключим далее).")