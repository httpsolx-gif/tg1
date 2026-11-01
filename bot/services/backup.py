"""Бэкапы и выгрузка всех БД.

- `dump_database()` — дамп основной БД (Postgres → pg_dump|gzip; sqlite → копия .gz)
  в `deploy/backups/`, с ротацией.
- `fetch_node_clicks(node)` — забрать sqlite-лог переходов с origin-ноды по SSH.
- `full_export()` — дамп БД + clicks.db со всех готовых shorter-нод (список файлов).
- `backup_to_channel(bot)` — дамп + отправка в канал бэкапов (OTHER → 📣 Каналы / .env);
  ушедший в канал файл удаляется с сервера, `sweep_to_channel` уносит хвосты (п.118).

«Всегда» = периодическая задача в main.py шлёт дамп в Telegram-канал (оффсайт).
"""
from __future__ import annotations

import asyncio
import base64
import datetime as _dt
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

from common.config import settings

log = logging.getLogger("bot")

ROOT = Path(__file__).resolve().parents[2]
BACKUP_DIR = ROOT / "deploy" / "backups"
KEEP = 14  # сколько локальных дампов хранить
TG_UPLOAD_LIMIT = 50 * 1024 * 1024  # лимит Bot API на send_document


def _ts() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _rotate() -> None:
    for pattern in ("db_*.sql.gz", "db_*.sqlite.gz", "clicks_*.sqlite"):
        files = sorted(BACKUP_DIR.glob(pattern), key=lambda p: p.stat().st_mtime)
        for p in (files[:-KEEP] if len(files) > KEEP else []):
            try:
                p.unlink()
            except Exception:
                pass


async def dump_database() -> Path:
    """Сделать дамп основной БД в deploy/backups и вернуть путь к .gz."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    url = settings.DATABASE_URL
    if url.startswith("sqlite"):
        # sqlite:///path → gzip копия файла
        import gzip
        import shutil
        src = url.split("///", 1)[-1] or ":memory:"
        out = BACKUP_DIR / f"db_{_ts()}.sqlite.gz"
        with open(src, "rb") as fi, gzip.open(out, "wb") as fo:
            shutil.copyfileobj(fi, fo)
        _rotate()
        return out

    u = urlparse(url)  # postgresql+psycopg://user:pass@host:port/dbname
    host = u.hostname or "localhost"
    port = str(u.port or 5432)
    user = u.username or ""
    dbname = (u.path or "/").lstrip("/")
    out = BACKUP_DIR / f"db_{_ts()}.sql.gz"
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password
    import shlex
    parts = ["pg_dump", "-h", host, "-p", port]
    if user:
        parts += ["-U", user]
    parts += ["-d", dbname, "--no-owner", "--no-privileges"]
    cmd = " ".join(shlex.quote(x) for x in parts) + f" | gzip -9 > {shlex.quote(str(out))}"
    # bash -c + pipefail: иначе returncode = код gzip (0), и падение pg_dump
    # проехало бы как успех → в канал улетел бы пустой/битый дамп.
    proc = await asyncio.create_subprocess_exec(
        "bash", "-c", "set -o pipefail; " + cmd,
        env=env, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE)
    _, err = await proc.communicate()
    if proc.returncode != 0:
        try:
            out.unlink()
        except Exception:
            pass
        raise RuntimeError(f"pg_dump failed: {(err or b'').decode('utf-8','ignore')[:300]}")
    _rotate()
    return out


async def fetch_node_clicks(node) -> Path | None:
    """Забрать /var/lib/shortcf/clicks.db с ноды по SSH (base64) → локальный файл."""
    from bot.services.node_runner import _run_on_node
    try:
        r = await _run_on_node(
            node, "test -f /var/lib/shortcf/clicks.db && base64 /var/lib/shortcf/clicks.db || true",
            timeout=60)
    except Exception as e:
        log.warning("fetch clicks %s: %s", getattr(node, "ip", "?"), e)
        return None
    data = (r.stdout or "").strip()
    if not data:
        return None
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = BACKUP_DIR / f"clicks_{getattr(node, 'ip', 'node')}_{_ts()}.sqlite"
    try:
        out.write_bytes(base64.b64decode(data))
    except Exception as e:
        log.warning("decode clicks %s: %s", getattr(node, "ip", "?"), e)
        return None
    return out


async def full_export() -> list[Path]:
    """Дамп основной БД + clicks.db со всех готовых shorter-нод. → список файлов."""
    files: list[Path] = [await dump_database()]
    from sqlmodel import Session, select
    from common.db import get_engine
    from common.models.short_domain import ShortDomain
    from common.models.node import Node
    with Session(get_engine()) as db:
        node_ids = {d.node_id for d in db.exec(
            select(ShortDomain).where(ShortDomain.status == "ready")).all() if d.node_id}
        nodes = [db.get(Node, nid) for nid in node_ids]
        for n in nodes:
            if n:
                db.expunge(n)
    for n in nodes:
        if n:
            f = await fetch_node_clicks(n)
            if f:
                files.append(f)
    return files


def _unlink(p: Path) -> None:
    try:
        p.unlink()
    except Exception:
        pass


async def _send_file(bot, chan: str, path: Path, caption: str) -> tuple[bool, str]:
    """Файл в канал бэкапов; ушёл — удаляем с сервера (п.118: не копим дампы на диске)."""
    from aiogram.types import FSInputFile
    size = path.stat().st_size
    if size > TG_UPLOAD_LIMIT:
        # Bot API не примет файл > 50 МБ — оставляем на сервере, иначе потеряем бэкап
        return False, (f"{path.name}: {size / 1024 / 1024:.1f} МБ больше лимита Telegram 50 МБ — "
                       f"не отправлен, лежит на сервере: {path}")
    try:
        await bot.send_document(chan, FSInputFile(str(path)), caption=caption)
    except Exception as e:
        return False, f"{path.name}: send: {e}"
    _unlink(path)
    return True, path.name


async def sweep_to_channel(bot) -> tuple[int, list[str]]:
    """Все дампы, что лежат в deploy/backups (перед деплоем, выгрузки, неотправленные), —
    в канал и с диска. → (отправлено, ошибки)."""
    from common import channels
    chan = channels.get("backup")
    if not chan or not BACKUP_DIR.exists():
        return 0, []
    sent, errs = 0, []
    files = sorted((p for pat in ("db_*.sql.gz", "db_*.sqlite.gz", "clicks_*.sqlite")
                    for p in BACKUP_DIR.glob(pat)), key=lambda p: p.stat().st_mtime)
    for p in files:
        when = _dt.datetime.fromtimestamp(p.stat().st_mtime)
        ok, info = await _send_file(bot, chan, p, f"🗄 {p.name} · {when:%Y-%m-%d %H:%M} · {p.stat().st_size // 1024} КБ")
        if ok:
            sent += 1
        else:
            errs.append(info)
    return sent, errs


async def backup_to_channel(bot) -> tuple[bool, str]:
    """Сделать дамп БД и отправить в канал бэкапов (после отправки файл удаляется). (ok, инфо)."""
    from common import channels
    chan = channels.get("backup")
    if not chan:
        return False, "канал бэкапов не задан (OTHER → 📣 Каналы)"
    try:
        path = await dump_database()
    except Exception as e:
        return False, str(e)
    cap = f"🗄 Бэкап БД {_dt.datetime.now():%Y-%m-%d %H:%M} · {path.stat().st_size // 1024} КБ"
    ok, info = await _send_file(bot, chan, path, cap)
    if ok:
        await sweep_to_channel(bot)  # заодно — хвосты (дамп перед деплоем и т.п.)
    return ok, info


async def backup_loop(bot) -> None:
    """Фон: раз в BACKUP_EVERY_HOURS — дамп в канал; каждый час — хвосты из deploy/backups
    в канал и с диска. Канал задаётся в боте — проверяем на каждом шаге."""
    hours = int(getattr(settings, "BACKUP_EVERY_HOURS", 0) or 0)
    last_dump = 0.0
    import time
    await asyncio.sleep(60)  # после старта: деплой уже проверен — его дамп можно уносить
    while True:
        try:
            if hours > 0 and time.time() - last_dump >= hours * 3600:
                ok, info = await backup_to_channel(bot)
                if ok or "не задан" not in info:
                    log.info("auto-backup: %s %s", "ok" if ok else "FAIL", info)
                    if not ok:
                        log.warning("авто-бэкап не ушёл в канал: %s", info)
                last_dump = time.time() if ok else last_dump
            sent, errs = await sweep_to_channel(bot)
            if sent:
                log.info("backup sweep: %s файл(ов) → канал, удалены с диска", sent)
            for e in errs:
                log.warning("бэкап не ушёл в канал: %s", e)
        except Exception as e:
            log.warning("backup loop: %s", e)
        await asyncio.sleep(3600)
