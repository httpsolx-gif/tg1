import logging
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.types import Message

from bot.keyboards.main_menu import main_menu
from common.config import settings

router = Router()
log = logging.getLogger("bot")

@router.message(CommandStart())
async def start(m: Message):
    is_admin = (m.from_user.id in settings.admin_ids_set)
    await m.answer(
        "Привет! Выберите действие.",
        reply_markup=main_menu(is_admin=is_admin),
    )

# Кнопку «📤 Отправить письмо» обрабатывает flow_send (см. bot/handlers/flow_send.py)

@router.message(F.text.in_({"📋 Меню", "Меню"}))
async def user_menu(m: Message):
    await m.answer("Пока тут пусто. Основные функции в «Отправить письмо» и «Админка».")