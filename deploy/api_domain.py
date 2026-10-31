"""Выставить публичный API на свой домен через Cloudflare + nginx (один раз, идемпотентно).

    # на сервере бота (root), после bootstrap.sh:
    sudo .venv/bin/python deploy/api_domain.py grzl.cc
    # только DNS (можно с любой машины с доступом к базе/токенам), IP сервера — явно:
    .venv/bin/python deploy/api_domain.py grzl.cc --ip 23.27.22.210 --dns-only

Шаги: зона в CF → NS у NameSilo на CF → SSL Full(strict) + Always HTTPS → A-запись (проксируемая)
на IP сервера → origin-сертификат CF (ключ генерится тут же и не покидает сервер) → nginx :443 →
127.0.0.1:API_PORT, доступ только с IP Cloudflare (API доверяет CF-Connecting-IP — напрямую в обход
CF ходить нельзя). Порт 80 → редирект на https.

Токены: env CF_TOKEN / NAMESILO_KEY, иначе из бота: OTHER → 🔑 Ключи (или последний short-домен). Без NAMESILO_KEY NS надо сменить у регистратора руками (скрипт их покажет).
"""
import argparse
import asyncio
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR))
os.chdir(PROJECT_DIR)  # common.config читает .env из cwd

from bot.services import cloudflare as cf  # noqa: E402
from bot.services import namesilo  # noqa: E402

SSL_DIR = Path("/etc/ssl/mailer-api")
NGINX_CONF = Path("/etc/nginx/conf.d/mailer-api.conf")


def log(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def tokens_from_db() -> tuple[str, str]:
    """(cf_token, namesilo_key) из последнего short-домена, где они заданы."""
    try:
        from sqlmodel import Session, select
        from common.db import get_engine
        from common.models.short_domain import ShortDomain
        from common.security import decrypt_password
        from bot.services import keys  # общие ключи (OTHER → 🔑 Ключи) — в приоритете
        with Session(get_engine()) as db:
            rows = db.exec(select(ShortDomain).order_by(ShortDomain.id.desc())).all()
        cf_tok = keys.get("cf") or next((decrypt_password(d.cf_token_enc) for d in rows if d.cf_token_enc), "")
        ns_key = keys.get("namesilo") or next((decrypt_password(d.registrar_token_enc) for d in rows
                                              if d.registrar_token_enc and (d.registrar or "") == "namesilo"), "")
        return cf_tok, ns_key
    except Exception as e:
        print(f"   (токены из базы не прочитаны: {e})")
        return "", ""


def public_ip() -> str:
    """Внешний IPv4 этого сервера (через trace Cloudflare)."""
    with urllib.request.urlopen("https://1.1.1.1/cdn-cgi/trace", timeout=10) as r:
        for line in r.read().decode().splitlines():
            if line.startswith("ip="):
                return line[3:].strip()
    raise RuntimeError("не удалось определить внешний IP — передай --ip")


def api_port() -> int:
    for line in (PROJECT_DIR / ".env").read_text().splitlines():
        if line.startswith("API_PORT="):
            return int(line.split("=", 1)[1].strip() or 8080)
    return 8080


async def setup_dns(domain: str, ip: str, cf_tok: str, ns_key: str) -> str:
    log(f"Cloudflare: зона {domain}")
    zone_id, cf_ns = await cf.create_zone(cf_tok, domain)
    status = await cf.zone_status(cf_tok, zone_id)
    print(f"   zone {zone_id} · {status} · NS CF: {', '.join(cf_ns)}")

    if ns_key:
        cur = [n.lower().rstrip(".") for n in await namesilo.get_nameservers(ns_key, domain)]
        if sorted(cur) == sorted(n.lower() for n in cf_ns):
            print("   NameSilo: NS уже на Cloudflare")
        else:
            log(f"NameSilo: NS {', '.join(cur)} → {', '.join(cf_ns)}")
            print("   " + await namesilo.change_nameservers(ns_key, domain, cf_ns))
            await cf.activation_check(cf_tok, zone_id)
    elif status != "active":
        print(f"   ⚠️ нет NAMESILO_KEY — поменяй NS у регистратора на: {', '.join(cf_ns)}")

    log("SSL Full (strict) + Always Use HTTPS")
    await cf.set_ssl(cf_tok, zone_id, "strict")
    await cf.always_use_https(cf_tok, zone_id, True)

    log(f"A {domain} → {ip} (proxied)")
    await cf.upsert_a_record(cf_tok, zone_id, domain, ip, proxied=True)
    return status


def run(*cmd: str, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=True, text=True, **kw)


async def ensure_cert(domain: str, cf_tok: str) -> tuple[Path, Path]:
    key, crt = SSL_DIR / "key.pem", SSL_DIR / "cert.pem"
    if key.exists() and crt.exists():
        out = run("openssl", "x509", "-in", str(crt), "-noout", "-ext", "subjectAltName",
                  capture_output=True).stdout
        if f"DNS:{domain}" in out:
            print(f"   origin-сертификат уже есть ({crt})")
            return key, crt
    log("origin-сертификат CF (ключ — локально)")
    SSL_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(SSL_DIR, 0o700)
    # ключ+CSR во временной папке; на место кладём парой только после выпуска (старая пара цела при ошибке)
    with tempfile.TemporaryDirectory(dir=SSL_DIR) as tmp:
        tkey, csr = Path(tmp) / "key.pem", Path(tmp) / "req.csr"
        run("openssl", "req", "-new", "-newkey", "rsa:2048", "-nodes", "-keyout", str(tkey),
            "-out", str(csr), "-subj", f"/CN={domain}", capture_output=True)
        os.chmod(tkey, 0o600)
        pem, cert_id = await cf.create_origin_cert(cf_tok, [domain, f"*.{domain}"], csr.read_text())
        tkey.replace(key)
        crt.write_text(pem)
    print(f"   выпущен {cert_id} → {crt}")
    return key, crt


def nginx_conf(domain: str, port: int, key: Path, crt: Path, v4: list[str], v6: list[str]) -> str:
    allow = "\n".join(f"    allow {c};" for c in v4 + v6)
    # realip НЕ включаем: allow/deny должны проверять адрес CF, а не клиента; IP клиента API
    # берёт сам из CF-Connecting-IP (api/auth._client_ip)
    return f"""# mailer API на {domain} — сгенерировано deploy/api_domain.py, правки затрутся при повторном запуске
server {{
    listen 80;
    listen [::]:80;
    server_name {domain};
    return 301 https://$host$request_uri;
}}

server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name {domain};

    ssl_certificate     {crt};
    ssl_certificate_key {key};
    ssl_protocols TLSv1.2 TLSv1.3;

    # только Cloudflare: API доверяет CF-Connecting-IP (ip_whitelist токенов, аудит)
{allow}
    deny all;

    client_max_body_size 2m;

    location / {{
        proxy_pass http://127.0.0.1:{port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header CF-Connecting-IP $http_cf_connecting_ip;
        proxy_set_header X-Forwarded-For $http_cf_connecting_ip;
        proxy_read_timeout 60s;
    }}
}}
"""


async def setup_nginx(domain: str, cf_tok: str) -> None:
    if os.geteuid() != 0:
        sys.exit("серверная часть (nginx, сертификат) — только от root: sudo .venv/bin/python deploy/api_domain.py …")
    if not shutil.which("nginx"):
        log("apt: nginx")
        run("apt-get", "install", "-y", "nginx", env={**os.environ, "DEBIAN_FRONTEND": "noninteractive"})
    key, crt = await ensure_cert(domain, cf_tok)
    v4, v6 = await cf.cf_ip_ranges()
    port = api_port()
    log(f"nginx: {NGINX_CONF} → 127.0.0.1:{port}")
    old = NGINX_CONF.read_text() if NGINX_CONF.exists() else None
    NGINX_CONF.write_text(nginx_conf(domain, port, key, crt, v4, v6))
    # `http2 on;` — nginx ≥1.25; на старом — флаг в listen
    if subprocess.run(["nginx", "-t"], capture_output=True).returncode != 0:
        NGINX_CONF.write_text(NGINX_CONF.read_text()
                              .replace("    http2 on;\n", "")
                              .replace("listen 443 ssl;", "listen 443 ssl http2;")
                              .replace("listen [::]:443 ssl;", "listen [::]:443 ssl http2;"))
    t = subprocess.run(["nginx", "-t"], capture_output=True, text=True)
    if t.returncode != 0:
        if old is None:
            NGINX_CONF.unlink()
        else:
            NGINX_CONF.write_text(old)
        sys.exit(f"nginx -t не прошёл, конфиг откатан:\n{t.stderr}")
    run("systemctl", "enable", "--now", "nginx")
    run("systemctl", "reload", "nginx")

    # проверка в обход DNS: nginx → API
    code = subprocess.run(
        ["curl", "-sk", "-o", "/dev/null", "-w", "%{http_code}", "--resolve", f"{domain}:443:127.0.0.1",
         f"https://{domain}/"], capture_output=True, text=True).stdout
    # с 127.0.0.1 nginx отвечает 403 (не CF) — это и есть ожидаемое «закрыто для всех, кроме CF»
    print(f"   локальная проверка: HTTP {code} (403 = nginx пускает только Cloudflare — ок)")
    api = subprocess.run(["curl", "-s", "-o", "/dev/null", "-w", "%{http_code}", f"http://127.0.0.1:{port}/"],
                         capture_output=True, text=True).stdout
    print(f"   API на 127.0.0.1:{port}: HTTP {api}" + ("" if api not in ("", "000") else
          " ⚠️ не отвечает — systemctl status mailer-api"))


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("domain")
    ap.add_argument("--ip", help="IP сервера для A-записи (по умолчанию — внешний IP этой машины)")
    ap.add_argument("--dns-only", action="store_true", help="только Cloudflare/NameSilo, без nginx")
    a = ap.parse_args()
    domain = a.domain.lower().strip().rstrip(".")

    db_cf, db_ns = tokens_from_db()
    cf_tok = os.getenv("CF_TOKEN") or db_cf
    ns_key = os.getenv("NAMESILO_KEY") or db_ns
    if not cf_tok:
        sys.exit("нет токена Cloudflare: CF_TOKEN=… или добавь short-домен в боте")
    ok, detail = await cf.verify_token(cf_tok)
    if not ok:
        sys.exit(f"токен Cloudflare не работает: {detail}")

    ip = a.ip or public_ip()
    status = await setup_dns(domain, ip, cf_tok, ns_key)
    if not a.dns_only:
        await setup_nginx(domain, cf_tok)

    print()
    print(f"✅ {domain} → {ip}")
    if status != "active":
        print("   зона CF ещё pending — заработает, когда NS обновятся (обычно минуты, до суток)")
    print(f"   проверка снаружи: curl -s -o /dev/null -w '%{{http_code}}\\n' https://{domain}/   (404 = API отвечает)")
    print(f"   клиенту: база https://{domain}/api/v1 , дока — api/CLIENT.md"
          f" (или API_DOCS=1 в .env → https://{domain}/docs)")


if __name__ == "__main__":
    asyncio.run(main())
