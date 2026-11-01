"""Защита нод: фаервол (вход закрыт, кроме SSH и web для short) + fail2ban на SSH.

Ставится сама: фон `guard_loop` (у лидера, раз в минуту) сравнивает желаемый набор
правил (`desired_sig`) с применённым (`node.fw_applied`) и докатывает разницу —
новые ноды, смена роли/порта, новая версия правил. Кнопка на карточке — то же сразу.

Защита от потери доступа: новые правила применяются вместе с таймером отката
(systemd-run, ROLLBACK_SEC); подтверждение — НОВЫМ SSH-подключением. Не зашли —
таймер вернёт прошлые правила (или снимет таблицу), автозагрузка не включается.

80/443 здесь просто пропускаем для ролей short/both: ограничение «только IP
Cloudflare» делает отдельная таблица shortcf (short_provision). В nftables пакет
должен принять КАЖДЫЙ базовый chain хука, поэтому без этого accept наш policy drop
резал бы сокращалку.
"""
from __future__ import annotations

import asyncio
import ipaddress
import logging
import time

from sqlmodel import Session, select

from common.config import settings
from common.db import get_engine
from common.models.appstate import AppState
from common.models.node import Node
from bot.services.node_runner import DEFAULT_SSH_PORT, NodeUnreachable, _run_on_node

log = logging.getLogger("bot")
_engine = get_engine()

VERSION = "v2"  # v2: белый список IP — все порты + разбан
CONF_DIR = "/etc/nodefw"
ROLLBACK_SEC = 120
RETRY_SEC = 30 * 60  # после ошибки — повтор не чаще раза в 30 мин (кнопка — сразу)

_busy: set[int] = set()
_failed_at: dict[int, float] = {}

# root — как есть, иначе sudo без пароля; IP бота (кто подключился) — аргументом,
# т.к. sudo вычищает SSH_CLIENT
_WRAP = ('if [ "$(id -u)" = 0 ]; then bash -s -- "${SSH_CLIENT%% *}"; '
         'else sudo -n bash -s -- "${SSH_CLIENT%% *}"; fi')


TRUST_KEY = "nodefw:trust"  # AppState: белый список IP из бота (NODES → 🤍)


def norm_ip(x: str) -> str | None:
    try:
        return str(ipaddress.ip_network(x.strip(), strict=False))
    except ValueError:
        return None


def ui_trust() -> list[str]:
    """Белый список, заданный в боте."""
    with Session(_engine) as db:
        row = db.get(AppState, TRUST_KEY)
    return [x for x in (row.value or "").split() if norm_ip(x)] if row else []


def set_ui_trust(ips: list[str]) -> None:
    with Session(_engine) as db:
        row = db.get(AppState, TRUST_KEY) or AppState(key=TRUST_KEY)
        row.value = " ".join(sorted(set(ips)))
        db.add(row)
        db.commit()


def _trust_ips() -> list[str]:
    """Белый список = NODE_TRUST_IPS (.env) + список из бота: все порты открыты, fail2ban не банит."""
    out = []
    for x in (settings.NODE_TRUST_IPS or "").replace(",", " ").split() + ui_trust():
        net = norm_ip(x)
        if net:
            out.append(net)
        else:
            log.warning("белый список нод: пропускаю %r", x)
    return sorted(set(out))


def _port(n: Node) -> int:
    return int(n.port or DEFAULT_SSH_PORT)


def _web(n: Node) -> bool:
    return n.has_role("short")


def desired_sig(n: Node) -> str:
    return f"{VERSION}|ssh={_port(n)}|web={int(_web(n))}|trust={','.join(_trust_ips())}"


def _trust_rules(trust: list[str]) -> str:
    v4 = [t for t in trust if ":" not in t]
    v6 = [t for t in trust if ":" in t]
    out = ""
    if v4:
        out += f"        ip saddr {{ {', '.join(v4)} }} accept  # белый список\n"
    if v6:
        out += f"        ip6 saddr {{ {', '.join(v6)} }} accept  # белый список\n"
    return out


def nft_ruleset(ssh_port: int, web: bool, trust: list[str] | None = None) -> str:
    web_rule = "        tcp dport { 80, 443 } accept  # CF-only — в таблице shortcf\n" if web else ""
    return f"""#!/usr/sbin/nft -f
# nodefw (mailer): входящие закрыты, кроме SSH{' и 80/443' if web else ''}. Исходящие не трогаем.
table inet nodefw
delete table inet nodefw
table inet nodefw {{
    chain input {{
        type filter hook input priority 0; policy drop;
        iif "lo" accept
        ct state established,related accept
        ct state invalid drop
{_trust_rules(trust or [])}        ip protocol icmp accept
        meta l4proto ipv6-icmp accept
        udp dport {{ 68, 546 }} accept  # DHCP-ответы (v4/v6)
        tcp dport {ssh_port} accept
{web_rule}    }}
}}
"""


def jail_conf(ssh_port: int, trust: list[str]) -> str:
    # $1 подставит bash на ноде (IP бота) — чтобы бот не забанил сам себя
    ignore = " ".join(["127.0.0.1/8", "::1", "$BOT_IP", *trust])
    return f"""# mailer: fail2ban на SSH ноды
[sshd]
enabled = true
port = {ssh_port}
backend = systemd
banaction = nftables-multiport
banaction_allports = nftables-allports
maxretry = 5
findtime = 10m
bantime = 1h
bantime.increment = true
bantime.maxtime = 1w
ignoreip = {ignore}
"""


_UNIT = f"""[Unit]
Description=mailer node firewall (nodefw)
After=network-pre.target
Before=network.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f {CONF_DIR}/nodefw.nft
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""

_ROLLBACK = (f"[ -f {CONF_DIR}/nodefw.nft ] && /usr/sbin/nft -f {CONF_DIR}/nodefw.nft "
             "|| /usr/sbin/nft delete table inet nodefw")


def apply_script(ssh_port: int, web: bool, trust: list[str] | None = None) -> str:
    """Шаг 1: пакеты, проверка правил, таймер отката, применить (без автозагрузки)."""
    return f"""set -e
export DEBIAN_FRONTEND=noninteractive
need=""
command -v nft >/dev/null 2>&1 || need="$need nftables"
command -v fail2ban-client >/dev/null 2>&1 || need="$need fail2ban"
python3 -c 'import systemd.journal' >/dev/null 2>&1 || need="$need python3-systemd"
if [ -n "$need" ]; then
  command -v apt-get >/dev/null 2>&1 || {{ echo "нет apt-get, нужны:$need" >&2; exit 3; }}
  apt-get update -qq >/dev/null
  apt-get install -y -qq $need >/dev/null
fi
mkdir -p {CONF_DIR}
cat > {CONF_DIR}/nodefw.nft.new <<'NFT'
{nft_ruleset(ssh_port, web, trust)}NFT
nft -c -f {CONF_DIR}/nodefw.nft.new
systemctl stop nodefw-rollback.timer nodefw-rollback.service >/dev/null 2>&1 || true
systemctl reset-failed nodefw-rollback.timer nodefw-rollback.service >/dev/null 2>&1 || true
systemd-run --quiet --unit=nodefw-rollback --on-active={ROLLBACK_SEC} /bin/sh -c '{_ROLLBACK}'
nft -f {CONF_DIR}/nodefw.nft.new
echo APPLIED
"""


def confirm_script(ssh_port: int, trust: list[str]) -> str:
    """Шаг 2 (новое SSH-подключение): снять откат, закрепить правила, fail2ban."""
    return f"""set -e
BOT_IP="$1"
case "$BOT_IP" in *[!0-9a-fA-F:.]*|"") BOT_IP="";; esac
systemctl stop nodefw-rollback.timer >/dev/null 2>&1 || true
mv -f {CONF_DIR}/nodefw.nft.new {CONF_DIR}/nodefw.nft
cat > /etc/systemd/system/nodefw.service <<'UNIT'
{_UNIT}UNIT
systemctl daemon-reload
systemctl enable nodefw >/dev/null 2>&1
echo FW_OK
mkdir -p /etc/fail2ban/jail.d
cat > /etc/fail2ban/jail.d/mailer-sshd.conf <<JAIL
{jail_conf(ssh_port, trust)}JAIL
systemctl enable fail2ban >/dev/null 2>&1
systemctl restart fail2ban
for i in 1 2 3 4 5 6 7 8 9 10; do
  if fail2ban-client status sshd >/dev/null 2>&1; then
    for ip in {' '.join(t for t in trust if '/' not in t or t.endswith(('/32', '/128')))}; do
      fail2ban-client set sshd unbanip "${{ip%/*}}" >/dev/null 2>&1 || true
    done
    echo F2B_OK; exit 0
  fi
  sleep 1
done
echo "fail2ban не поднял jail sshd" >&2
exit 4
"""


async def _step(node, script: str, timeout: int) -> str:
    r = await _run_on_node(node, _WRAP, input=script, timeout=timeout)
    out, err = (r.stdout or "").strip(), (r.stderr or "").strip()
    if r.exit_status != 0:
        raise RuntimeError(f"exit {r.exit_status}: {(out + ' ' + err).strip()[-300:]}")
    return out


async def harden(node) -> tuple[bool, str]:
    """Поставить/обновить защиту на ноду. → (ok, текст для лога/карточки)."""
    port, web, trust = _port(node), _web(node), _trust_ips()
    try:
        out = await _step(node, apply_script(port, web, trust), timeout=300)
        if "APPLIED" not in out:
            return False, f"правила не применились: {out[-200:]}"
    except NodeUnreachable as e:
        return False, f"нода недоступна по SSH: {e}"
    except Exception as e:
        return False, f"фаервол: {e}"
    # подтверждаем новым подключением: значит после правил бот по-прежнему заходит
    try:
        out = await _step(node, confirm_script(port, trust), timeout=90)
    except NodeUnreachable as e:
        return False, (f"после правил SSH не пускает ({e}) — через {ROLLBACK_SEC} с "
                       "нода сама откатит фаервол")
    except Exception as e:
        return False, f"fail2ban: {e}" if "FW_OK" in str(e) else f"закрепление: {e}"
    if "F2B_OK" not in out:
        return False, f"fail2ban: {out[-200:]}"
    return True, f"фаервол (SSH :{port}{', 80/443' if web else ''}) + fail2ban"


async def ensure(nid: int, bot=None, *, force: bool = False) -> tuple[str, str | None]:
    """Довести ноду до желаемой защиты. → ('ok'|'skip'|'busy'|'error'|'missing', info)."""
    if nid in _busy:
        return "busy", None
    _busy.add(nid)
    try:
        with Session(_engine) as db:
            n = db.get(Node, nid)
            if not n:
                return "missing", None
            sig = desired_sig(n)
            if not force and n.fw_applied == sig:
                return "skip", None
            prev_err = n.fw_error
            db.expunge(n)
        ok, info = await harden(n)
        with Session(_engine) as db:
            row = db.get(Node, nid)
            if row:
                row.fw_applied = sig if ok else row.fw_applied
                row.fw_error = None if ok else info[:500]
                db.add(row)
                db.commit()
        if ok:
            _failed_at.pop(nid, None)
            log.info("node_guard: %s защищена — %s", n.ip, info)
            return "ok", info
        _failed_at[nid] = time.time()
        log.warning("node_guard: %s — %s", n.ip, info)
        if bot and info != prev_err and "недоступна" not in info:
            await _notify(bot, f"🔴 Защита ноды <b>{n.ip}</b>: <code>{_esc(info[:300])}</code>")
        return "error", info
    finally:
        _busy.discard(nid)


def _esc(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


async def _notify(bot, text: str) -> None:
    for uid in settings.admin_ids_set:
        try:
            await bot.send_message(uid, text)
        except Exception:
            pass


async def guard_loop(bot) -> None:
    """Фон (у лидера): все ноды — новые, сменившие роль/порт — получают защиту сами."""
    while True:
        await asyncio.sleep(60)
        try:
            with Session(_engine) as db:
                nodes = db.exec(select(Node).order_by(Node.id)).all()
                todo = [n.id for n in nodes if n.fw_applied != desired_sig(n)]
            now = time.time()
            for nid in todo:
                if now - _failed_at.get(nid, 0) < RETRY_SEC:
                    continue
                await ensure(nid, bot)
        except Exception as e:
            log.warning("node_guard loop: %s", e)
