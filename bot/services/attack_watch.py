"""Уведомления об атаках → канал ошибок (WARNING) (п.118).

Фон у лидера, раз в CHECK_SEC:
- 🔨 брутфорс SSH: прирост `Total banned` fail2ban на каждой защищённой ноде;
- 🌊 флуд сокращалки: прирост 429 / сброшенных соединений из stats.json сервиса shortcf
  (заодно докатывает новую версию server.py на ноды, где она устарела — иначе счётчиков нет);
- ☁️ Cloudflare rate limit: сколько запросов зона отрезала на краю (GraphQL Analytics).

Пороги — за один интервал. Первый замер после старта — только база, без алерта.
"""
from __future__ import annotations

import asyncio
import datetime as _dt
import hashlib
import json
import logging
import re

from sqlmodel import Session, select

from common.db import get_engine
from common.models.node import Node
from common.models.short_domain import ShortDomain

log = logging.getLogger("bot")
_engine = get_engine()

CHECK_SEC = 300
SSH_BAN_ALERT = 5      # новых банов fail2ban за интервал
FLOOD_ALERT = 300      # 429 + сброшенных соединений за интервал на ноде
CF_RL_ALERT = 300      # запросов, отрезанных rate limit CF, за интервал на зону

_prev_bans: dict[int, int] = {}
_prev_flood: dict[int, tuple[float, int, int]] = {}  # node_id → (started, r429, drop)
_cf_warned: set[str] = set()


def _top(d: dict | list, n: int = 5) -> str:
    items = d.items() if isinstance(d, dict) else d
    return ", ".join(f"{ip} ×{c}" for ip, c in sorted(items, key=lambda kv: -kv[1])[:n])


# ---------- SSH (fail2ban) ----------
_BAN_RE = re.compile(r"Total banned:\s*(\d+)")
_CUR_RE = re.compile(r"Currently banned:\s*(\d+)")
_LIST_RE = re.compile(r"Banned IP list:\s*(.*)")


async def _ssh_bans(n: Node) -> None:
    from bot.services.node_runner import _run_on_node
    r = await _run_on_node(n, "fail2ban-client status sshd 2>/dev/null || sudo -n fail2ban-client status sshd",
                           timeout=30)
    out = r.stdout or ""
    m = _BAN_RE.search(out)
    if not m:
        return
    total = int(m.group(1))
    prev = _prev_bans.get(n.id)
    _prev_bans[n.id] = total
    if prev is None or total < prev:  # первый замер / fail2ban перезапускался — счётчик с нуля
        return
    new = total - prev
    if new >= SSH_BAN_ALERT:
        cur = _CUR_RE.search(out)
        ips = (_LIST_RE.search(out).group(1).split() if _LIST_RE.search(out) else [])[-5:]
        log.warning("🔨 Брутфорс SSH на ноде %s: fail2ban забанил %s IP за %s мин (сейчас в бане %s)%s",
                    n.ip, new, CHECK_SEC // 60, cur.group(1) if cur else "?",
                    f" · последние: {', '.join(ips)}" if ips else "")


# ---------- флуд сокращалки ----------
def _src_sha() -> str:
    from bot.services.short_domain import REDIRECT_SERVICE_SRC
    return hashlib.sha256(REDIRECT_SERVICE_SRC.encode()).hexdigest()


async def _refresh_service(n: Node) -> bool:
    """server.py на ноде устарел → докатить и перезапустить. → True, если обновили."""
    from bot.services.node_runner import _run_on_node
    from bot.services.short_provision import APP_DIR, _write_file
    from bot.services.short_domain import REDIRECT_SERVICE_SRC
    r = await _run_on_node(n, f"sha256sum {APP_DIR}/server.py 2>/dev/null | cut -d' ' -f1", timeout=20)
    if (r.stdout or "").strip() == _src_sha():
        return False
    await _write_file(n, f"{APP_DIR}/server.py", REDIRECT_SERVICE_SRC, mode="755")
    await _run_on_node(n, "systemctl restart shortcf", timeout=40)
    log.info("attack_watch: shortcf на %s обновлён (счётчики флуда)", n.ip)
    return True


async def _flood(n: Node) -> None:
    from bot.services.node_runner import _run_on_node
    if await _refresh_service(n):
        _prev_flood.pop(n.id, None)
        return
    r = await _run_on_node(n, "cat /var/lib/shortcf/stats.json 2>/dev/null", timeout=20)
    try:
        st = json.loads(r.stdout or "")
    except Exception:
        return
    cur = (float(st.get("started") or 0), int(st.get("r429") or 0), int(st.get("drop") or 0))
    prev = _prev_flood.get(n.id)
    _prev_flood[n.id] = cur
    if prev is None or prev[0] != cur[0]:  # первый замер / сервис перезапускался
        return
    d429, ddrop = cur[1] - prev[1], cur[2] - prev[2]
    if d429 + ddrop >= FLOOD_ALERT:
        top = _top(st.get("top") or {})
        log.warning("🌊 Флуд на сокращалку (нода %s): за %s мин отбито %s запросов (429) и сброшено %s "
                    "соединений%s", n.ip, CHECK_SEC // 60, d429, ddrop, f" · топ IP: {top}" if top else "")


# ---------- Cloudflare rate limit ----------
async def _cf_rl(token: str, d: ShortDomain, since: str) -> None:
    from bot.services import cloudflare as cf
    try:
        total, top = await cf.ratelimit_hits(token, d.cf_zone_id, since)
    except Exception as e:
        if d.domain not in _cf_warned:  # один раз на домен за жизнь процесса, без спама
            _cf_warned.add(d.domain)
            log.info("attack_watch: аналитика rate limit CF для %s недоступна (%s) — "
                     "у токена нужно право Zone → Analytics → Read", d.domain, str(e)[:150])
        return
    if total >= CF_RL_ALERT:
        log.warning("☁️ Cloudflare rate limit (%s): за %s мин отрезано %s запросов · топ IP: %s",
                    d.domain, CHECK_SEC // 60, total, _top(top))


async def check_once() -> None:
    since = (_dt.datetime.utcnow() - _dt.timedelta(seconds=CHECK_SEC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    with Session(_engine) as db:
        nodes = db.exec(select(Node).where(Node.is_active == True)).all()  # noqa: E712
        doms = db.exec(select(ShortDomain).where(ShortDomain.status == "ready")).all()
        for x in (*nodes, *doms):
            db.expunge(x)
    short_ids = {d.node_id for d in doms if d.node_id}
    for n in nodes:
        try:
            if n.fw_applied:
                await _ssh_bans(n)
            if n.id in short_ids:
                await _flood(n)
        except Exception as e:
            log.debug("attack_watch %s: %s", n.ip, e)
    if doms:
        from bot.services import keys
        from common.security import decrypt_password
        for d in doms:
            token = keys.get("cf") or decrypt_password(d.cf_token_enc or "")
            if token and d.cf_zone_id:
                await _cf_rl(token, d, since)


async def watch_loop(bot=None) -> None:
    await asyncio.sleep(60)
    while True:
        try:
            await check_once()  # первый проход — база для приростов
        except Exception as e:
            log.warning("attack_watch: %s", e)
        await asyncio.sleep(CHECK_SEC)
