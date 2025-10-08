from common.logging.setup import configure_logging
from common.config import settings
from aiogram import Dispatcher

from bot.handlers.start import router as start_router
from bot.handlers.admin.index import router as admin_router
from bot.handlers.admin.smtp import router as smtp_router
from bot.handlers.admin.short import router as short_router

configure_logging(settings.BOT_TOKEN)

def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    dp.include_router(start_router)
    dp.include_router(admin_router)
    dp.include_router(smtp_router)
    dp.include_router(short_router)
    return dp