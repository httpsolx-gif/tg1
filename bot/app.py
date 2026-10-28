from common.logging.setup import configure_logging
from common.config import settings
from aiogram import Dispatcher

from bot.handlers.start import router as start_router
from bot.handlers.flow_send import router as flow_send_router
from bot.handlers.admin.index import router as admin_router
from bot.handlers.admin.smtp import router as smtp_router
from bot.handlers.admin.short import router as short_router
from bot.handlers.admin.nodes import router as nodes_router
from bot.handlers.admin.tpl import router as tpl_router
from bot.handlers.admin.rubrics import router as rubrics_router
from bot.handlers.admin.scope_ops import router as scope_ops_router
from bot.handlers.admin.short_domains import router as short_domains_router
from bot.handlers.admin.stats import router as stats_router
from bot.handlers.admin.backup import router as backup_router
from bot.handlers.admin.test import router as test_router
from bot.handlers.admin.users import router as users_router
from bot.handlers.admin.sends import router as sends_router
from bot.handlers.admin.api import router as api_router
from bot.handlers.admin.mailtypes import router as mailtypes_router
from bot.handlers.admin.smtp_limits import router as smtp_limits_router
from bot.middlewares.error_report import ErrorReportMiddleware

configure_logging(settings.BOT_TOKEN)

def build_dispatcher() -> Dispatcher:
    dp = Dispatcher()
    # Глобальный перехват ошибок обработчиков (логирование + короткий ответ юзеру)
    dp.update.outer_middleware(ErrorReportMiddleware())

    dp.include_router(start_router)
    dp.include_router(flow_send_router)
    dp.include_router(admin_router)
    dp.include_router(smtp_router)
    dp.include_router(short_router)
    dp.include_router(nodes_router)
    dp.include_router(tpl_router)
    dp.include_router(rubrics_router)
    dp.include_router(scope_ops_router)
    dp.include_router(short_domains_router)
    dp.include_router(stats_router)
    dp.include_router(backup_router)
    dp.include_router(test_router)
    dp.include_router(users_router)
    dp.include_router(sends_router)
    dp.include_router(api_router)
    dp.include_router(mailtypes_router)
    dp.include_router(smtp_limits_router)
    return dp