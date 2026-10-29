"""Список групп/каналов, где состоит аккаунт — чтобы добавить их в рассылку (п.56).

Запуск:  .venv/bin/python -m userbot.list_dialogs
Печатает тип, id (-100…), @username и название. Для приватных групп берите id —
его и вставляйте в админку (📣 BROADCAST → Группы). Публичные можно по @username.
"""
import asyncio

from telethon import TelegramClient

from common.config import settings


async def main() -> None:
    client = TelegramClient(settings.USERBOT_SESSION, settings.TG_API_ID, settings.TG_API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        print("❌ Нет сессии. Сначала: .venv/bin/python -m userbot.login")
        await client.disconnect()
        return
    print(f"{'тип':7} | {'id':>16} | {'username':22} | название")
    print("-" * 70)
    async for d in client.iter_dialogs():
        if not (d.is_group or d.is_channel):
            continue
        ent = d.entity
        uname = getattr(ent, "username", None)
        is_mega = getattr(ent, "megagroup", False)
        kind = "группа" if (d.is_group or is_mega) else "канал"
        print(f"{kind:7} | {d.id:>16} | {('@'+uname) if uname else '-':22} | {d.name}")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
