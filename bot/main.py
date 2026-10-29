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
from common.leader import LeaderLock

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
        # резервная нода отправки (п.59)
        "ALTER TABLE node ADD COLUMN IF NOT EXISTS is_backup boolean NOT NULL DEFAULT false",
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
        # API-отправки (п.46): привязка SendLog к токену + данные клиента (id/username)
        "ALTER TABLE sendlog ADD COLUMN IF NOT EXISTS api_token_id integer",
        "ALTER TABLE sendlog ADD COLUMN IF NOT EXISTS ext_id varchar",
        "ALTER TABLE sendlog ADD COLUMN IF NOT EXISTS ext_username varchar",
        "CREATE INDEX IF NOT EXISTS ix_sendlog_api_token_id ON sendlog (api_token_id)",
        # статус отправки клиенту по job_id (п.54)
        "ALTER TABLE sendlog ADD COLUMN IF NOT EXISTS job_id varchar",
        "CREATE INDEX IF NOT EXISTS ix_sendlog_job_id ON sendlog (job_id)",
    ]
    with engine.begin() as conn:
        for s in stmts:
            try:
                conn.execute(text(s))
            except Exception as e:
                logging.getLogger("bot").warning("ensure column failed: %s (%s)", s, e)


async def main():
    configure_logging(settings.BOT_TOKEN)

    # Схема БД — ДО лидер-лока: резерв с новым кодом тоже добавляет колонки (IF NOT EXISTS,
    # идемпотентно), иначе обновлённый API/резерв падал бы на отсутствующей колонке.
    try:
        engine = get_engine()
        # регистрируем модели, создаваемые через create_all (не в alembic)
        import common.models.short_domain  # noqa: F401
        import common.models.access  # noqa: F401  (AccessEntry, SeenUser)
        import common.models.appstate  # noqa: F401  (AppState — курсор выгрузки /sends)
        import common.models.api_token  # noqa: F401  (ApiToken — токены публичного API)
        import common.models.suppression  # noqa: F401  (Suppression — не слать на адрес)
        import common.models.api_audit  # noqa: F401  (ApiAudit — аудит вызовов API)
        import common.models.mx_cache  # noqa: F401  (MxCache — провайдер по MX)
        import common.models.spamcheck  # noqa: F401  (SpamSeed, SpamCheckResult — авто-проверка на спам)
        import common.models.broadcast  # noqa: F401  (BcState, BcTarget — авторассылка по группам, userbot)
        SQLModel.metadata.create_all(engine)
        _ensure_columns(engine)
    except Exception as e:
        logging.getLogger("bot").error("DB init failed: %s", e)

    # Лидер-лок (п.57): второй экземпляр на другом сервере ждёт здесь в резерве.
    # Всё ниже (delete_webhook, фоновые циклы, polling) — только у лидера.
    leader = LeaderLock("tg1:bot")
    await leader.acquire()
    leader.start_watchdog()

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

    # Зарезервированная рубрика «black» (профиль black-отправителей) — всегда должна быть
    try:
        from bot.handlers.admin.rubrics import ensure_black_rubric
        ensure_black_rubric()
    except Exception as e:
        logging.getLogger("bot").warning("ensure black rubric failed: %s", e)

    try:
        # False: при переключении на резерв не терять сообщения юзеров, пришедшие за время передачи
        await bot.delete_webhook(drop_pending_updates=False)
    except Exception as e:
        logging.getLogger("bot").warning("delete_webhook error: %s", e)

    # Авто-бэкап БД в Telegram-канал (если заданы BACKUP_CHANNEL_ID и BACKUP_EVERY_HOURS)
    try:
        from bot.services.backup import backup_loop
        asyncio.create_task(backup_loop(bot))
    except Exception as e:
        logging.getLogger("bot").warning("backup loop not started: %s", e)

    # Авто-проверка на спам (если заданы SPAMCHECK_CHANNEL_ID и SPAMCHECK_EVERY_MIN)
    try:
        from bot.services.spamcheck import spamcheck_loop
        asyncio.create_task(spamcheck_loop(bot))
    except Exception as e:
        logging.getLogger("bot").warning("spamcheck loop not started: %s", e)

    logging.getLogger("bot").info("🚀 Bot started")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger("bot").info("Stopped")
