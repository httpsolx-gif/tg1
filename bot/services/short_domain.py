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
import datetime, json, os, socket, sqlite3, ssl, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CONF_DIR = "/etc/shortcf"
CLICK_DB = os.environ.get("SHORTCF_DB", "/var/lib/shortcf/clicks.db")
PORT = int(os.environ.get("SHORTCF_PORT", "443"))

# анти-флуд: TLS-рукопожатие в потоке соединения с таймаутом (не в accept), потолок
# одновременных соединений, таймаут простоя, лимит запросов на посетителя (CF-Connecting-IP)
MAX_CONN = int(os.environ.get("SHORTCF_MAX_CONN", "200"))
IO_TIMEOUT = float(os.environ.get("SHORTCF_TIMEOUT", "10"))
RATE_N = int(os.environ.get("SHORTCF_RATE_N", "30"))       # запросов
RATE_WIN = float(os.environ.get("SHORTCF_RATE_WIN", "10"))  # за столько секунд

_DBLOCK = threading.Lock()
_RLOCK = threading.Lock()
_HITS = {}  # ip -> [начало окна, счётчик]
_LAST_PRUNE = [0.0]

# счётчики для бота (мониторинг флуда, п.118): накопительные с запуска → stats.json раз в 30 с
STATS_FILE = os.environ.get("SHORTCF_STATS", "/var/lib/shortcf/stats.json")
_STAT = {"started": time.time(), "req": 0, "r429": 0, "drop": 0}
_TOP = {}  # ip -> сколько раз получил 429

def stat(name, ip=None):
    with _RLOCK:
        _STAT[name] += 1
        if ip is not None:
            _TOP[ip] = _TOP.get(ip, 0) + 1
            if len(_TOP) > 5000:  # не даём словарю расти при флуде с тысяч IP
                for k, _ in sorted(_TOP.items(), key=lambda kv: kv[1])[:2500]:
                    del _TOP[k]

def _stats_writer():
    while True:
        time.sleep(30)
        try:
            with _RLOCK:
                d = dict(_STAT)
                d["top"] = dict(sorted(_TOP.items(), key=lambda kv: -kv[1])[:10])
            tmp = STATS_FILE + ".tmp"
            with open(tmp, "w") as f:
                json.dump(d, f)
            os.replace(tmp, STATS_FILE)
        except Exception as e:
            sys.stderr.write("stats: %s\n" % e)

def rate_ok(ip, now=None):
    now = time.monotonic() if now is None else now
    with _RLOCK:
        if now - _LAST_PRUNE[0] > RATE_WIN:  # чистка старых окон — память не растёт
            for k in [k for k, v in _HITS.items() if now - v[0] >= RATE_WIN]:
                del _HITS[k]
            _LAST_PRUNE[0] = now
        v = _HITS.get(ip)
        if v is None or now - v[0] >= RATE_WIN:
            _HITS[ip] = [now, 1]
            return True
        v[1] += 1
        return v[1] <= RATE_N

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
    timeout = IO_TIMEOUT  # медленные заголовки / простой keep-alive — соединение рвём
    server_version = ""  # без «BaseHTTP/x Python/x» — не светим стек
    sys_version = ""
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
        host = self._host()
        if host not in CONFS:  # чужой Host (сканер/чужая CF-зона на наш IP) — молча рвём
            self.close_connection = True
            return
        ip = self.headers.get("CF-Connecting-IP") or self.client_address[0]
        stat("req")
        if not rate_ok(ip):
            stat("r429", ip)
            self.close_connection = True
            self._send(429, b"", {"Retry-After": str(int(RATE_WIN)), "Connection": "close"})
            return
        if path in ("healthz", "favicon.ico", ""):
            self._send(200, b"ok", {"Content-Type": "text/plain"})
            return
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
        # без SNI или с чужим именем — рвём handshake: сертификат с нашим доменом
        # не отдаём никому, кто не знает домен (защита, если фаервол вдруг слетит)
        if not servername or servername.lower() not in per:
            return ssl.ALERT_DESCRIPTION_UNRECOGNIZED_NAME
        sock.context = per[servername.lower()]
    ctx.sni_callback = sni
    return ctx

class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 512

    def __init__(self, addr, handler, ctx):
        self.ctx = ctx
        self.slots = threading.BoundedSemaphore(MAX_CONN)
        ThreadingHTTPServer.__init__(self, addr, handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):  # потолок соединений — новые сразу закрываем
            stat("drop")
            self.shutdown_request(request)
            return
        try:
            ThreadingHTTPServer.process_request(self, request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            ThreadingHTTPServer.process_request_thread(self, request, client_address)
        finally:
            self.slots.release()

    def finish_request(self, request, client_address):
        # рукопожатие здесь (поток соединения, с таймаутом), а не в accept: иначе один
        # молчащий клиент блокировал бы приём всех остальных
        request.settimeout(IO_TIMEOUT)
        try:
            tls = self.ctx.wrap_socket(request, server_side=True)
        except (ssl.SSLError, OSError):
            return
        try:
            self.RequestHandlerClass(tls, client_address, self)
        finally:
            try:
                tls.close()
            except OSError:
                pass

    def handle_error(self, request, client_address):
        e = sys.exc_info()[1]
        if not isinstance(e, (socket.timeout, ConnectionError, ssl.SSLError)):
            ThreadingHTTPServer.handle_error(self, request, client_address)

def main():
    _init_db()
    threading.Thread(target=_stats_writer, daemon=True).start()
    httpd = Server(("0.0.0.0", PORT), H, build_ctx())
    sys.stderr.write("shortcf listening on :%d (%d domains, max %d conn, %d req/%ds per ip)\n"
                     % (PORT, len(CONFS), MAX_CONN, RATE_N, RATE_WIN))
    httpd.serve_forever()

if __name__ == "__main__":
    main()
'''


SYSTEMD_UNIT = """[Unit]
Description=shortcf redirect service
# сначала фаервол CF-only, потом слушаем 443 (и при загрузке тоже)
After=network.target shortcf-fw.service
Requires=shortcf-fw.service

[Service]
ExecStart=/usr/bin/env python3 /opt/shortcf/server.py
Restart=always
RestartSec=2
AmbientCapabilities=CAP_NET_BIND_SERVICE
NoNewPrivileges=yes

[Install]
WantedBy=multi-user.target
"""
