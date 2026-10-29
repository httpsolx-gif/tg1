"""Разовый вход userbot ПОД аккаунтом пользователя (п.56).

Запуск:  .venv/bin/python -m userbot.login
Спросит номер телефона, код из Telegram и (если включена) 2FA-пароль. Создаёт файл
сессии (settings.USERBOT_SESSION). Дальше userbot/main.py работает без кода.

Код и пароль вводите САМИ — это вход в ваш аккаунт.
"""
import asyncio

from telethon import TelegramClient

from common.config import settings


async def main() -> None:
    if not settings.TG_API_ID or not settings.TG_API_HASH:
        print("❌ Укажите TG_API_ID и TG_API_HASH в .env (получить на https://my.telegram.org → API development tools).")
        return
    client = TelegramClient(settings.USERBOT_SESSION, settings.TG_API_ID, settings.TG_API_HASH)
    await client.start(phone=(settings.USERBOT_PHONE or None))
    me = await client.get_me()
    uname = f"@{me.username}" if me.username else "(без username)"
    print(f"✅ Вход выполнен: {me.first_name} {uname} · id={me.id}")
    print(f"Сессия сохранена: {settings.USERBOT_SESSION}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
