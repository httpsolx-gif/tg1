import logging
from aiogram import Router, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from bot.keyboards.main_menu import main_menu
from bot.keyboards.icons import strip_lead_emoji as _strip
from common.config import settings

router = Router()
log = logging.getLogger("bot")

@router.message(CommandStart())
async def start(m: Message, state: FSMContext):
    is_admin = (m.from_user.id in settings.admin_ids_set)
    # нижнее меню (📨/📋/🛠) + сразу выбор страны → сервиса, без лишнего шага
    await m.answer("Привет!", reply_markup=main_menu(is_admin=is_admin))
    from bot.handlers.flow_send import begin_send
    await begin_send(m, state, greet="✉️ Отправка письма.")

# Кнопку «✉ Отправить письмо» обрабатывает flow_send (см. bot/handlers/flow_send.py)

@router.message(F.text.func(lambda t: isinstance(t, str) and _strip(t) == "Меню"))
async def user_menu(m: Message):
    await m.answer("Пока тут пусто. Основные функции в «Отправить письмо» и «Админка».")