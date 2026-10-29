"""userbot — авторассылка фото+текст по группам ОТ ИМЕНИ аккаунта (п.56).

Запуск:  .venv/bin/python -m userbot.main
Читает конфиг/цели из общей БД (раздел 📣 BROADCAST в боте) и постит по кругу раз в
interval_min. Антифлуд: рандомные паузы между группами, обработка FloodWait, лимит
сообщений в сутки.

⚠️ Это автоматизация ВАШЕГО пользовательского аккаунта — формально против ToS Telegram.
Держите интервалы человеческими и список групп скромным, иначе аккаунт поймает
FloodWait/бан. Это не обходится «прогревом/прокси» — такого тут нет и не будет.
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import sys
from datetime import datetime

from sqlmodel import SQLModel
from telethon import TelegramClient
from telethon.errors import FloodWaitError
from telethon.extensions import html as tl_html

from common.config import settings
from common.db import get_engine
from common.leader import LeaderLock
from common.logging.setup import configure_logging
import common.models.broadcast  # noqa: F401  (регистрация таблиц)
from common import broadcast_store as store

log = logging.getLogger("userbot")


def _parse_ref(ref: str):
    """Строку-цель → то, что принимает get_entity: @username / числовой id / ссылку."""
    r = (ref or "").strip()
    if r.startswith("@"):
        return r
    if "t.me/" in r:
        tail = r.split("t.me/", 1)[1].split("?", 1)[0].strip("/")
        if tail.startswith("+") or tail.startswith("joinchat/"):
            return r  # invite-ссылку оставляем как есть (Telethon резолвит сам)
        parts = tail.split("/")
        if parts[0] == "c" and len(parts) > 1 and parts[1].isdigit():
            return int("-100" + parts[1])  # t.me/c/123/45 — приватный канал/супергруппа
        return parts[0]  # t.me/name или t.me/name/123 (ссылка на пост) → name
    try:
        return int(r)
    except ValueError:
        return r


async def _entity(client: TelegramClient, ref):
    try:
        return await client.get_entity(ref)
    except ValueError:
        # числовой id резолвится только из кэша сессии — подтянуть диалоги и повторить
        if not isinstance(ref, int):
            raise
        await client.get_dialogs()
        return await client.get_entity(ref)


async def _send_one(client: TelegramClient, target, caption: str, media_path: str | None) -> None:
    ent = await _entity(client, _parse_ref(target.chat_ref))
    # caption — HTML (бот сохраняет m.html_text)
    if media_path:
        await client.send_file(ent, media_path, caption=caption or "", parse_mode="html")
    else:
        await client.send_message(ent, caption or "", parse_mode="html")


def _check_content(caption: str, media_path: str | None) -> str | None:
    """Проверка до прохода: иначе каждая группа упадёт одинаково (и каждая ждёт паузу)."""
    if media_path and not os.path.exists(media_path):
        return f"фото не найдено на этом сервере: {media_path}"
    plain, _ = tl_html.parse(caption or "")
    if not media_path and not plain.strip():
        return "нет ни текста, ни фото"
    limit = 1024 if media_path else 4096
    if len(plain) > limit:
        return f"текст {len(plain)} симв. > лимита {limit}" + (" (подпись к фото)" if media_path else "")
    return None


async def run_once(client: TelegramClient, manual: bool = False) -> str:
    """Один полный проход по активным группам. Возвращает краткий итог."""
    store.reset_day_if_needed()
    st = store.get_state()
    targets = store.list_targets(active_only=True)
    if not targets:
        store.update_state(last_run=datetime.utcnow().isoformat(), status="нет активных групп")
        return "нет активных групп"
    problem = _check_content(st.caption, st.media_path)
    if problem:
        store.update_state(last_run=datetime.utcnow().isoformat(), status=f"⛔ не отправлено: {problem}")
        return problem
    ok = fail = 0
    for t in targets:
        store.reset_day_if_needed()  # проход мог перейти через полночь
        cur = store.get_state()
        if not manual and not cur.enabled:
            log.info("рассылку выключили посреди прохода — останавливаюсь")
            break
        if cur.daily_cap and cur.sent_today >= cur.daily_cap:
            log.info("daily cap reached (%s)", cur.daily_cap)
            break
        try:
            await _send_one(client, t, st.caption, st.media_path)
            store.mark_target(t.id, ok=True)
            store.bump_sent()
            ok += 1
        except FloodWaitError as e:
            wait = min(int(e.seconds) + 5, 3600)
            log.warning("FloodWait %ss на %s — ждём", e.seconds, t.chat_ref)
            store.mark_target(t.id, ok=False, info=f"floodwait {e.seconds}s")
            await asyncio.sleep(wait)
            continue
        except Exception as e:
            fail += 1
            store.mark_target(t.id, ok=False, info=str(e))
            log.warning("send %s: %s", t.chat_ref, e)
        lo = st.delay_min or 8
        hi = max(st.delay_max or 25, lo)
        await asyncio.sleep(random.uniform(lo, hi))
    summary = f"✅ {ok} · ⛔ {fail} · {datetime.now():%H:%M}"
    store.update_state(last_run=datetime.utcnow().isoformat(), status=summary)
    return summary


def _due(st) -> bool:
    if not st.last_run:
        return True
    try:
        last = datetime.fromisoformat(st.last_run)
    except Exception:
        return True
    return (datetime.utcnow() - last).total_seconds() >= (st.interval_min or 60) * 60


async def main() -> None:
    configure_logging(settings.BOT_TOKEN)
    if not settings.TG_API_ID or not settings.TG_API_HASH:
        log.error("TG_API_ID/TG_API_HASH не заданы — заполни .env (my.telegram.org)")
        sys.exit(78)  # EX_CONFIG: systemd не перезапускает (RestartPreventExitStatus)
    # Лидер-лок (п.57): резервный userbot ждёт здесь, пока основной жив
    leader = LeaderLock("tg1:userbot")
    await leader.acquire()
    leader.start_watchdog()
    SQLModel.metadata.create_all(get_engine())
    store.ensure_state()

    client = TelegramClient(settings.USERBOT_SESSION, settings.TG_API_ID, settings.TG_API_HASH)
    await client.connect()
    if not await client.is_user_authorized():
        log.error("нет сессии — выполни один раз: .venv/bin/python -m userbot.login")
        await client.disconnect()
        sys.exit(78)
    me = await client.get_me()
    log.info("🤖 userbot запущен как @%s id=%s", getattr(me, "username", None), me.id)
    await client.get_dialogs()  # кэш сущностей: иначе числовые id групп не резолвятся

    try:
        while True:
            st = store.get_state()
            # «Отправить сейчас» — разовый проход и при выключенной рассылке
            manual = bool(st.run_now)
            if manual or (st.enabled and _due(st)):
                if manual:
                    store.update_state(run_now=False)
                log.info("broadcast run (manual=%s)", manual)
                summary = await run_once(client, manual=manual)
                log.info("broadcast done: %s", summary)
            await asyncio.sleep(15)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("userbot stopped")
