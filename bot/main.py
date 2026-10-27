import asyncio, logging
from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from common.config import settings
from common.logging.setup import configure_logging
from bot.app import build_dispatcher
from sqlmodel import SQLModel
from common.db import get_engine

async def main():
    configure_logging(settings.BOT_TOKEN)

    session_kwargs = {}
    if settings.TG_PROXY:
        session_kwargs["proxy"] = settings.TG_PROXY
    session = AiohttpSession(**session_kwargs)

    bot = Bot(
        token=settings.BOT_TOKEN,
        session=session,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    # Кастом-эмодзи iOS-иконки: инлайн-кнопки (icon_custom_emoji_id) + текст (richify)
    try:
        from bot.keyboards.icons import IconRequestMiddleware, RichifyRequestMiddleware
        if IconRequestMiddleware is not None:
            bot.session.middleware(IconRequestMiddleware())
        if RichifyRequestMiddleware is not None:
            bot.session.middleware(RichifyRequestMiddleware())
    except Exception as e:
        logging.getLogger("bot").warning("emoji middleware not attached: %s", e)

    dp = build_dispatcher()

    # Initialize database tables once at startup
    try:
        engine = get_engine()
        SQLModel.metadata.create_all(engine)
    except Exception as e:
        logging.getLogger("bot").error("DB init failed: %s", e)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logging.getLogger("bot").warning("delete_webhook error: %s", e)

    logging.getLogger("bot").info("🚀 Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger("bot").info("Stopped")
