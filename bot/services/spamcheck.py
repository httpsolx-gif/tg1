"""Авто-проверка на спам (п.55) — ядро.

Идея: по каждому активному сид-ящику шлём пробное письмо через наше же ядро
(`send_core.send_payload`) с уникальным токеном в теме, ждём, заходим по IMAP и
смотрим, в какой папке письмо (Входящие / Спам). Из заголовков тянем
SPF/DKIM/DMARC (`Authentication-Results`) и спам-скор (`X-Spam-*`). Пишем
`SpamCheckResult` и постим карточку в `SPAMCHECK_CHANNEL_ID`.

Честные ограничения: один ящик = один провайдер, различаем только Входящие vs Спам
(Gmail-вкладки по IMAP не видны), нужен app-password (IMAP при 2FA). Поэтому
содержимое/токен варьируем, чтобы фильтры не резали пробы как дубль.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import logging
import re
import secrets
from datetime import datetime
from html import escape as _esc
from email.message import Message
from typing import Optional

from sqlmodel import Session, select

from common.config import settings
from common.db import get_engine
from common.models.spamcheck import SpamSeed, SpamCheckResult

IMAP_TIMEOUT = 30  # сек на подключение/операцию IMAP в авто-проверке

log = logging.getLogger("bot")
_engine = get_engine()

# ------------------------------- IMAP автоопределение -------------------------------
# domain → (imap_host, port, ssl, label). Подписи провайдеров для карточки.
_IMAP_MAP: dict[str, tuple[str, int, bool, str]] = {}


def _reg(hosts: str, server: str, label: str, port: int = 993, ssl: bool = True):
    for d in hosts.split():
        _IMAP_MAP[d] = (server, port, ssl, label)


_reg("gmail.com googlemail.com", "imap.gmail.com", "Gmail")
_reg("outlook.com hotmail.com live.com msn.com hotmail.co.uk live.co.uk outlook.de hotmail.de",
     "outlook.office365.com", "Outlook")
_reg("yahoo.com yahoo.co.uk ymail.com rocketmail.com", "imap.mail.yahoo.com", "Yahoo")
_reg("aol.com", "imap.aol.com", "AOL")
_reg("icloud.com me.com mac.com", "imap.mail.me.com", "iCloud")
_reg("gmx.de gmx.net gmx.com gmx.at gmx.ch", "imap.gmx.net", "GMX")
_reg("web.de", "imap.web.de", "Web.de")
_reg("mail.ru bk.ru inbox.ru list.ru internet.ru", "imap.mail.ru", "Mail.ru")
_reg("yandex.ru yandex.com ya.ru", "imap.yandex.ru", "Yandex")
_reg("zoho.com zohomail.com", "imap.zoho.com", "Zoho")
_reg("seznam.cz", "imap.seznam.cz", "Seznam")
_reg("wp.pl", "imap.wp.pl", "WP.pl")
_reg("o2.pl", "poczta.o2.pl", "o2.pl")
_reg("interia.pl", "poczta.interia.pl", "Interia")
_reg("orange.fr wanadoo.fr", "imap.orange.fr", "Orange")
_reg("free.fr", "imap.free.fr", "Free.fr")
_reg("laposte.net", "imap.laposte.net", "Laposte")


def imap_info_for(seed: SpamSeed) -> tuple[str, int, bool, str]:
    """IMAP-параметры ящика: берём сохранённые, иначе автоопределяем по домену.
    Неизвестный домен → `imap.<домен>` (разумная догадка; ошибка видна в результате)."""
    domain = (seed.email.rsplit("@", 1)[-1] or "").strip().lower()
    host, port, ssl, label = _IMAP_MAP.get(domain, (f"imap.{domain}", 993, True, domain or "mail"))
    return (seed.imap_host or host, seed.imap_port or port,
            seed.imap_ssl if seed.imap_host else ssl, seed.label or label)


def guess_imap(email_addr: str) -> tuple[str, int, bool, str]:
    """Автоопределение для нового сида (host, port, ssl, label) по e-mail."""
    domain = (email_addr.rsplit("@", 1)[-1] or "").strip().lower()
    return _IMAP_MAP.get(domain, (f"imap.{domain}", 993, True, domain or "mail"))


# ------------------------------- отправка пробы -------------------------------
async def send_probe(seed: SpamSeed) -> tuple[bool, str, str, Optional[str], Optional[str]]:
    """Отправить пробу через наше ядро. Возвращает (ok, token, via, sender, detail).
    Токен уходит в тему — по нему потом ищем именно это письмо."""
    from bot.services.send_core import build_payload, send_payload, SendError

    token = secrets.token_hex(5)
    try:
        payload = build_payload(
            country=seed.country, service=seed.service,
            email=seed.email, link=settings.SPAMCHECK_LINK,
        )
    except SendError as e:
        return False, token, "—", None, str(e)
    except Exception as e:
        return False, token, "—", None, f"payload: {e}"

    # токен отдельным словом в теме (надёжно ищется IMAP SEARCH SUBJECT)
    payload["subject"] = f'{payload.get("subject") or "Notification"} · ref {token}'
    try:
        res = await send_payload(payload)
    except Exception as e:
        return False, token, "—", None, f"send: {e}"
    via = res.get("via") or "—"
    sender = res.get("from_disp")
    if not res.get("ok"):
        return False, token, via, sender, res.get("info") or "send failed"
    return True, token, via, sender, None


# ------------------------------- IMAP-проверка -------------------------------
def _search_fetch(M: imaplib.IMAP4, folder: str, token: str) -> Optional[Message]:
    """Выбрать папку, найти письмо с токеном в теме, вернуть его заголовки (или None)."""
    try:
        typ, _ = M.select(f'"{folder}"', readonly=True)
        if typ != "OK":
            return None
        typ, data = M.search(None, '(SUBJECT "%s")' % token)
        if typ != "OK" or not data or not data[0]:
            return None
        ids = data[0].split()
        if not ids:
            return None
        typ, md = M.fetch(ids[-1], "(BODY.PEEK[HEADER])")
        if typ != "OK" or not md:
            return None
        raw = b"".join(p[1] for p in md if isinstance(p, tuple) and p[1])
        return email.message_from_bytes(raw) if raw else None
    except Exception as e:
        log.debug("imap search %s: %s", folder, e)
        return None


def _junk_folder(M: imaplib.IMAP4) -> Optional[str]:
    """Найти папку Спам/Junk: сначала по спец-флагу \\Junk (RFC 6154), затем по имени."""
    try:
        typ, data = M.list()
    except Exception:
        return None
    if typ != "OK" or not data:
        return None
    flagged: list[str] = []
    named: list[str] = []
    for ln in data:
        s = ln.decode("utf-8", "ignore") if isinstance(ln, (bytes, bytearray)) else str(ln)
        low = s.lower()
        m = re.search(r'"([^"]*)"\s*$', s) or re.search(r'([^ ]+)\s*$', s)
        name = m.group(1) if m else ""
        if not name:
            continue
        if "\\junk" in low:
            flagged.append(name)
        elif "spam" in low or "junk" in low:
            named.append(name)
    return (flagged or named or [None])[0]


def _imap_check(seed: SpamSeed, token: str) -> tuple[str, Optional[Message]]:
    """Синхронный IMAP-заход: где лежит письмо. (placement, msg|None).
    placement: inbox|spam|missing. Ошибки логина/сети — исключением наружу."""
    host, port, ssl, _label = imap_info_for(seed)
    user = seed.imap_user or seed.email
    # timeout обязателен: «чёрная дыра» вместо IMAP-сервера навсегда занимала поток executor'а
    M = (imaplib.IMAP4_SSL(host, port, timeout=IMAP_TIMEOUT) if ssl
         else imaplib.IMAP4(host, port, timeout=IMAP_TIMEOUT))
    try:
        M.login(user, seed.imap_pass)
        msg = _search_fetch(M, "INBOX", token)
        if msg is not None:
            return "inbox", msg
        junk = _junk_folder(M)
        if junk:
            msg = _search_fetch(M, junk, token)
            if msg is not None:
                return "spam", msg
        return "missing", None
    finally:
        try:
            M.logout()
        except Exception:
            pass


def imap_login_test(email_addr: str, password: str, *, host: Optional[str] = None,
                    port: Optional[int] = None, ssl: Optional[bool] = None,
                    user: Optional[str] = None, timeout: int = 20) -> tuple[bool, str]:
    """Быстрая проверка IMAP-ящика: связь + логин + поиск папки Спам (без отправки).
    Возвращает (ok, человекочитаемая_инфо). Хост/порт/ssl не заданы → автоопределение по домену."""
    domain = (email_addr.rsplit("@", 1)[-1] or "").strip().lower()
    g_host, g_port, g_ssl, _label = _IMAP_MAP.get(domain, (f"imap.{domain}", 993, True, domain or "mail"))
    host = host or g_host
    port = port or g_port
    ssl = g_ssl if ssl is None else ssl
    user = user or email_addr
    try:
        M = (imaplib.IMAP4_SSL(host, port, timeout=timeout) if ssl
             else imaplib.IMAP4(host, port, timeout=timeout))
    except Exception as e:
        return False, f"нет связи с {host}:{port} ({str(e)[:70]})"
    try:
        M.login(user, password)
    except Exception as e:
        try:
            M.logout()
        except Exception:
            pass
        return False, f"логин отклонён ({str(e)[:90]})"
    junk = None
    try:
        junk = _junk_folder(M)
    except Exception:
        pass
    try:
        M.logout()
    except Exception:
        pass
    return True, (f"OK · папка Спам: {junk}" if junk else "OK · папку Спам не нашёл")


# ------------------------------- разбор заголовков -------------------------------
def parse_auth(msg: Message) -> tuple[Optional[str], Optional[str], Optional[str], Optional[str]]:
    """Достать spf/dkim/dmarc из Authentication-Results и спам-скор из X-Spam-*."""
    joined = " ".join(msg.get_all("Authentication-Results") or []).lower()

    def pick(key: str) -> Optional[str]:
        m = re.search(rf"\b{key}=([a-z]+)", joined)
        return m.group(1) if m else None

    spf, dkim, dmarc = pick("spf"), pick("dkim"), pick("dmarc")
    score = None
    xs = msg.get("X-Spam-Status") or msg.get("X-Spam-Score") or msg.get("X-Spam-Level")
    if xs:
        m = re.search(r"score=([-\d.]+)", xs) or re.search(r"([-\d.]+)", xs)
        if m:
            score = m.group(1)
    return spf, dkim, dmarc, score


# ------------------------------- одна проверка -------------------------------
async def check_one(seed: SpamSeed) -> dict:
    """Полный цикл по одному ящику: проба → ожидание → IMAP с ретраями → результат (persisted)."""
    ts = datetime.utcnow().isoformat()
    ok, token, via, sender, detail = await send_probe(seed)
    if not ok:
        return _persist(seed, ts, "send_fail", None, via=via, sender=sender, detail=detail)

    await asyncio.sleep(max(5, int(settings.SPAMCHECK_WAIT_SEC or 90)))
    loop = asyncio.get_event_loop()
    placement: Optional[str] = None
    msg: Optional[Message] = None
    ierr: Optional[str] = None
    for attempt in range(3):
        try:
            placement, msg = await loop.run_in_executor(None, _imap_check, seed, token)
            ierr = None
        except Exception as e:
            placement, msg, ierr = "imap_error", None, str(e)[:200]
            break
        if placement in ("inbox", "spam"):
            break
        if attempt < 2:
            await asyncio.sleep(60)

    if placement is None:
        placement = "missing"
    spf = dkim = dmarc = score = None
    if msg is not None:
        spf, dkim, dmarc, score = parse_auth(msg)
    return _persist(seed, ts, placement, score, spf=spf, dkim=dkim, dmarc=dmarc,
                    via=via, sender=sender, detail=ierr)


def _persist(seed: SpamSeed, ts: str, placement: str, score, *, spf=None, dkim=None,
             dmarc=None, via=None, sender=None, detail=None) -> dict:
    row = SpamCheckResult(
        seed_id=seed.id, ts=ts, placement=placement, spf=spf, dkim=dkim, dmarc=dmarc,
        spam_score=score, sender=sender, via=via, detail=detail,
    )
    try:
        with Session(_engine) as db:
            db.add(row)
            db.commit()
    except Exception as e:
        log.warning("SpamCheckResult persist: %s", e)
    _, _, _, label = imap_info_for(seed)
    return {"email": seed.email, "label": label, "placement": placement, "spf": spf,
            "dkim": dkim, "dmarc": dmarc, "score": score, "via": via,
            "sender": sender, "detail": detail}


# ------------------------------- прогон + карточка -------------------------------
async def run_once() -> list[dict]:
    """Проверить все активные сид-ящики (последовательно) и вернуть список результатов."""
    with Session(_engine) as db:
        seeds = db.exec(select(SpamSeed).where(SpamSeed.active == True).order_by(SpamSeed.id)).all()  # noqa: E712
        for s in seeds:
            db.expunge(s)
    results = []
    for s in seeds:
        try:
            results.append(await check_one(s))
        except Exception as e:
            log.warning("spamcheck seed %s: %s", getattr(s, "email", "?"), e)
    return results


_PLACE = {
    "inbox": "✅ Входящие",
    "spam": "🚫 СПАМ",
    "missing": "⌛ не найдено",
    "send_fail": "✈️❌ не ушло",
    "imap_error": "🔌 IMAP-ошибка",
}


def _auth_mark(v: Optional[str]) -> str:
    if v is None:
        return "➖"
    v = v.lower()
    if v in ("pass", "bestguesspass"):
        return "✅"
    if v in ("fail", "hardfail", "permerror"):
        return "❌"
    return "⚠️"  # softfail/neutral/none/temperror/unknown


def format_card(results: list[dict]) -> str:
    """Карточка в канал: по строке на ящик (размещение + auth + транспорт)."""
    head = f"🕵️ SPAM-check · {datetime.now():%H:%M}"
    if not results:
        return head + "\n\nНет активных ящиков для проверки."
    lines = [head, ""]
    for r in results:
        lines.append(f"📬 {_esc(r['email'])} ({_esc(r.get('label') or '—')})")
        place = _PLACE.get(r["placement"], r["placement"])
        auth = f"SPF{_auth_mark(r.get('spf'))} DKIM{_auth_mark(r.get('dkim'))} DMARC{_auth_mark(r.get('dmarc'))}"
        tail = ""
        if r["placement"] in ("inbox", "spam"):
            tail = f" · {auth}"
            if r.get("score"):
                tail += f" · score {_esc(str(r['score']))}"
            if r.get("via"):
                tail += f" · via {_esc(str(r['via']))}"
        elif r.get("detail"):
            tail = f" · <code>{_esc((r['detail'] or '')[:120])}</code>"
        lines.append(f"   {place}{tail}")
    return "\n".join(lines)


async def run_and_report(bot, *, post_empty: bool = False) -> tuple[list[dict], bool]:
    """Прогнать проверку и отправить карточку в SPAMCHECK_CHANNEL_ID. (results, posted)."""
    results = await run_once()
    from common import channels
    chan = channels.get("spam")
    posted = False
    if chan and (results or post_empty):
        try:
            await bot.send_message(chan, format_card(results))
            posted = True
        except Exception as e:
            log.warning("spamcheck report send: %s", e)
    return results, posted


async def spamcheck_loop(bot) -> None:
    """Фоновая задача: раз в SPAMCHECK_EVERY_MIN шлёт карточку в канал (0 = выкл)."""
    every = int(getattr(settings, "SPAMCHECK_EVERY_MIN", 0) or 0)
    if every <= 0:
        return
    from common import channels
    while True:
        if not channels.get("spam"):  # канал задают в боте — ждём, не гоняем проверку вхолостую
            await asyncio.sleep(60)
            continue
        try:
            _, posted = await run_and_report(bot)
            log.info("spamcheck: cycle done (posted=%s)", posted)
        except Exception as e:
            log.warning("spamcheck loop: %s", e)
        await asyncio.sleep(every * 60)
