"""Личный short-домен: короткий код → target хранится НА НОДЕ (stateful).

Бот при отправке генерит короткий код (base62, 7 символов) и регистрирует
соответствие код→target на origin-ноде по SSH (таблица `links` в том же sqlite,
что и клики). Нода при заходе на https://<domain>/<code> ищет код в БД и делает
301. Коды случайные и неугадываемые → резолвятся только зарегистрированные коды,
подделать чужой редирект нельзя (open-redirect исключён).

Нода недоступна в момент отправки → фолбэк: письмо уходит с ПОЛНОЙ исходной
ссылкой (без сокращения).
"""
from __future__ import annotations

import base64
import datetime
import json
import os
import secrets
import string
from typing import Optional

CODE_LEN = 7
_ALPHABET = string.ascii_letters + string.digits  # base62


def gen_secret() -> str:
    """Секрет домена (base64 32 байт). Исторический: в stateful-редиректе больше
    не используется, но пишется в конфиг ноды для совместимости."""
    return base64.b64encode(os.urandom(32)).decode("ascii")


def gen_code(n: int = CODE_LEN) -> str:
    """Короткий случайный код (base62)."""
    return "".join(secrets.choice(_ALPHABET) for _ in range(n))


def _norm_target(target: str) -> str:
    """Абсолютный URL для Location: без схемы → https:// (иначе 301 станет
    относительным и браузер уйдёт на <domain>/<target> → 'not found')."""
    t = (target or "").strip()
    if t and "://" not in t:
        t = "https://" + t
    return t


# Скрипт регистрации кода на ноде. Запускается `python3 -`; параметры передаются
# base64-JSON-блобом внутри самого скрипта (без шелл-инъекций целевого URL).
_REGISTER_SRC = r'''
import sqlite3, os, base64, json
DB = "/var/lib/shortcf/clicks.db"
d = json.loads(base64.b64decode("__B64__").decode("utf-8"))
os.makedirs(os.path.dirname(DB), exist_ok=True)
con = sqlite3.connect(DB, timeout=5)
con.execute("CREATE TABLE IF NOT EXISTS links ("
            "code TEXT PRIMARY KEY, domain TEXT, target TEXT, track TEXT, created TEXT)")
cur = con.execute("INSERT OR IGNORE INTO links (code,domain,target,track,created) VALUES (?,?,?,?,?)",
                  (d["code"], d["domain"], d["target"], d.get("track"), d["created"]))
con.commit(); con.close()
print("ok" if cur.rowcount else "dup")
'''


async def register_link(node, domain: str, target: str, track: str | None = None,
                        *, timeout: int = 30) -> Optional[str]:
    """Зарегистрировать короткий код на ноде → code, либо None (нода недоступна / нет python)."""
    from bot.services.node_runner import _run_on_node
    target = _norm_target(target)
    for _ in range(5):  # ретрай при крайне редкой коллизии кода
        code = gen_code()
        blob = base64.b64encode(json.dumps({
            "code": code, "domain": domain, "target": target,
            "track": track, "created": datetime.datetime.utcnow().isoformat(),
        }).encode("utf-8")).decode("ascii")
        src = _REGISTER_SRC.replace("__B64__", blob)
        got = None
        for interp in ("python3 -", "python -"):
            try:
                r = await _run_on_node(node, interp, input=src, timeout=timeout)
            except Exception:
                return None
            if r.exit_status == 127:  # интерпретатор не найден — пробуем следующий
                continue
            out = (r.stdout or "").strip()
            got = out.splitlines()[-1] if out else ""
            break
        else:
            return None  # ни python3, ни python на ноде
        if got == "ok":
            return code
        if got == "dup":
            continue  # коллизия кода — новый код
        return None   # иная ошибка на ноде
    return None


async def personal_short(provider: str, target: str, track: str | None = None) -> str:
    """provider='dom:{id}' → короткая личная ссылка (регистрация кода на ноде по SSH).
    Домен не готов / нет ноды / нода недоступна → исходный (полный) URL."""
    try:
        did = int(provider.split(":", 1)[1])
    except (ValueError, IndexError):
        return target
    from sqlmodel import Session
    from common.db import get_engine
    from common.models.short_domain import ShortDomain
    from common.models.node import Node
    with Session(get_engine()) as db:
        d = db.get(ShortDomain, did)
        if not (d and d.is_active and d.status == "ready" and d.node_id):
            return target
        domain = d.domain
        node = db.get(Node, d.node_id)
        if node is not None:
            db.expunge(node)  # работаем с нодой вне сессии
    if node is None:
        return target
    code = await register_link(node, domain, target, track)
    if not code:
        return target  # фолбэк: полная исходная ссылка
    return f"https://{domain}/{code}"


# ---------------------------------------------------------------------------
# Исходник мини-сервиса редиректа, который разворачивается на origin-ноде.
# stdlib-only (http.server + ssl). TLS с origin-сертификатом (выбор по SNI),
# код ищется в sqlite-таблице `links` (code+domain) → 301 на target.
# Конфиги доменов в CONF_DIR/*.json: {"domain","secret","cert","key"} (secret
# историчен, в редиректе не нужен; cert/key — для TLS).
# ---------------------------------------------------------------------------
REDIRECT_SERVICE_SRC = r'''#!/usr/bin/env python3
import datetime, json, os, sqlite3, ssl, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF_DIR = "/etc/shortcf"
CLICK_DB = os.environ.get("SHORTCF_DB", "/var/lib/shortcf/clicks.db")
PORT = int(os.environ.get("SHORTCF_PORT", "443"))

_DBLOCK = threading.Lock()

def _init_db():
    try:
        os.makedirs(os.path.dirname(CLICK_DB), exist_ok=True)
        con = sqlite3.connect(CLICK_DB, timeout=5)
        con.execute("CREATE TABLE IF NOT EXISTS clicks ("
                    "slug TEXT PRIMARY KEY, domain TEXT, target TEXT, track TEXT, ts TEXT)")
        con.execute("CREATE TABLE IF NOT EXISTS links ("
                    "code TEXT PRIMARY KEY, domain TEXT, target TEXT, track TEXT, created TEXT)")
        try:
            con.execute("ALTER TABLE clicks ADD COLUMN track TEXT")  # миграция старой таблицы
        except Exception:
            pass
        con.commit(); con.close()
    except Exception as e:
        sys.stderr.write("initdb: %s\n" % e)

def lookup_link(domain, code):
    try:
        with _DBLOCK:
            con = sqlite3.connect(CLICK_DB, timeout=5)
            r = con.execute("SELECT target, track FROM links WHERE code=? AND domain=?",
                            (code, domain)).fetchone()
            con.close()
        return (r[0], r[1]) if r else None
    except Exception as e:
        sys.stderr.write("lookup: %s\n" % e)
        return None

def log_click(domain, code, target, track):
    # 1 code = максимум 1 переход: INSERT OR IGNORE по первичному ключу slug(=code)
    try:
        with _DBLOCK:
            con = sqlite3.connect(CLICK_DB, timeout=5)
            con.execute("INSERT OR IGNORE INTO clicks (slug, domain, target, track, ts) VALUES (?,?,?,?,?)",
                        (code, domain, target, track, datetime.datetime.utcnow().isoformat()))
            con.commit(); con.close()
    except Exception as e:
        sys.stderr.write("clicklog: %s\n" % e)

def load_confs():
    confs = {}
    try:
        names = os.listdir(CONF_DIR)
    except Exception:
        names = []
    for fn in names:
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(CONF_DIR, fn)) as f:
                c = json.load(f)
            confs[c["domain"].lower()] = c
        except Exception as e:
            sys.stderr.write("conf %s: %s\n" % (fn, e))
    return confs

CONFS = load_confs()

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a):
        sys.stderr.write("%s - %s\n" % (self.address_string(), a[0] % a[1:]))
    def _host(self):
        h = self.headers.get("Host", "")
        return h.split(":", 1)[0].lower()
    def _send(self, code, body=b"", extra=None):
        self.send_response(code)
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD" and body:
            self.wfile.write(body)
    def do_GET(self):
        path = self.path.split("?", 1)[0].lstrip("/")
        if path in ("healthz", "favicon.ico", ""):
            self._send(200, b"ok", {"Content-Type": "text/plain"})
            return
        host = self._host()
        row = lookup_link(host, path)
        if not row:
            self._send(404, b"not found")
            return
        target, track = row
        if self.command != "HEAD":
            log_click(host, path, target, track)  # HEAD (превью/боты) не считаем
        self._send(301, b"", {"Location": target})
    do_HEAD = do_GET

def build_ctx():
    confs = list(CONFS.values())
    if not confs:
        sys.stderr.write("no domain confs in %s\n" % CONF_DIR); sys.exit(2)
    default = confs[0]
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(default["cert"], default["key"])
    per = {}
    for c in confs:
        pc = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        pc.load_cert_chain(c["cert"], c["key"])
        per[c["domain"].lower()] = pc
    def sni(sock, servername, sslctx):
        if servername and servername.lower() in per:
            sock.context = per[servername.lower()]
    ctx.sni_callback = sni
    return ctx

def main():
    _init_db()
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), H)
    httpd.socket = build_ctx().wrap_socket(httpd.socket, server_side=True)
    sys.stderr.write("shortcf listening on :%d (%d domains)\n" % (PORT, len(CONFS)))
    httpd.serve_forever()

if __name__ == "__main__":
    main()
'''


SYSTEMD_UNIT = """[Unit]
Description=shortcf redirect service
After=network.target

[Service]
ExecStart=/usr/bin/env python3 /opt/shortcf/server.py
Restart=always
RestartSec=2
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
"""
