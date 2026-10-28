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

def _ensure_columns(engine):
    """Идемпотентно добавить колонки, которых нет в create_all для уже существующих таблиц.

    create_all не трогает существующие таблицы — новые столбцы на старых таблицах
    добавляем вручную. Только Postgres (у sqlite-смоуков таблицы свежие из модели).
    """
    from sqlalchemy import text
    if engine.dialect.name != "postgresql":
        return
    stmts = [
        "ALTER TABLE node ADD COLUMN IF NOT EXISTS purpose varchar NOT NULL DEFAULT 'send'",
        "ALTER TABLE short_domain ADD COLUMN IF NOT EXISTS registrar varchar",
        "ALTER TABLE short_domain ADD COLUMN IF NOT EXISTS registrar_token_enc varchar",
        "ALTER TABLE sendlog ADD COLUMN IF NOT EXISTS country varchar",
        "ALTER TABLE sendlog ADD COLUMN IF NOT EXISTS service varchar",
        "ALTER TABLE sendlog ADD COLUMN IF NOT EXISTS mailbox varchar",
        "ALTER TABLE sendlog ADD COLUMN IF NOT EXISTS short_provider varchar",
        "ALTER TABLE sendlog ADD COLUMN IF NOT EXISTS track varchar",
        "CREATE INDEX IF NOT EXISTS ix_sendlog_track ON sendlog (track)",
        # aws_account: лимит/счётчик для единой очереди SMTP+AWS области (п.30)
        "ALTER TABLE aws_account ADD COLUMN IF NOT EXISTS send_limit integer NOT NULL DEFAULT 0",
        "ALTER TABLE aws_account ADD COLUMN IF NOT EXISTS sent_count integer NOT NULL DEFAULT 0",
        # автосмена: «по кругу» + per-scope счётчики на связках (один аккаунт в разных сервисах — раздельно)
        "ALTER TABLE scope_cfg ADD COLUMN IF NOT EXISTS wrap boolean NOT NULL DEFAULT false",
        "ALTER TABLE scope_smtp ADD COLUMN IF NOT EXISTS sent_count integer NOT NULL DEFAULT 0",
        "ALTER TABLE scope_aws ADD COLUMN IF NOT EXISTS sent_count integer NOT NULL DEFAULT 0",
    ]
    with engine.begin() as conn:
        for s in stmts:
            try:
                conn.execute(text(s))
            except Exception as e:
                logging.getLogger("bot").warning("ensure column failed: %s (%s)", s, e)


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
        # регистрируем модели, создаваемые через create_all (не в alembic)
        import common.models.short_domain  # noqa: F401
        import common.models.access  # noqa: F401  (AccessEntry, SeenUser)
        import common.models.appstate  # noqa: F401  (AppState — курсор выгрузки /sends)
        SQLModel.metadata.create_all(engine)
        _ensure_columns(engine)
    except Exception as e:
        logging.getLogger("bot").error("DB init failed: %s", e)

    # Зарезервированная рубрика «black» (профиль black-отправителей) — всегда должна быть
    try:
        from bot.handlers.admin.rubrics import ensure_black_rubric
        ensure_black_rubric()
    except Exception as e:
        logging.getLogger("bot").warning("ensure black rubric failed: %s", e)

    try:
        await bot.delete_webhook(drop_pending_updates=True)
    except Exception as e:
        logging.getLogger("bot").warning("delete_webhook error: %s", e)

    # Авто-бэкап БД в Telegram-канал (если заданы BACKUP_CHANNEL_ID и BACKUP_EVERY_HOURS)
    try:
        from bot.services.backup import backup_loop
        asyncio.create_task(backup_loop(bot))
    except Exception as e:
        logging.getLogger("bot").warning("backup loop not started: %s", e)

    logging.getLogger("bot").info("🚀 Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger("bot").info("Stopped")
