"""Провиженинг origin-ноды для личного short-домена (фаза B — сервер).

Фаза A (Cloudflare: зона → NS → active → SSL Full → проксированная A-запись) идёт
в хендлере до этого. Здесь — только серверная часть по SSH, когда CF уже закрывает
IP: генерим ключ+CSR на ноде, выпускаем origin-сертификат в CF, ставим мини-сервис
редиректа (systemd, TLS с origin-cert), закрываем 80/443 фаерволом только для IP CF,
проверяем 301 локально. В конце поднимаем SSL до strict.

Предполагается Debian/Ubuntu с root-доступом, python3, systemd. Каждый шаг
возвращает строку в лог; первая ошибка останавливает и пишется в last_error.
"""
from __future__ import annotations

import base64
from typing import Optional

from common.security import decrypt_password
from bot.services import cloudflare as cf
from bot.services.node_runner import _run_on_node
from bot.services.short_domain import REDIRECT_SERVICE_SRC, SYSTEMD_UNIT

CONF_DIR = "/etc/shortcf"
APP_DIR = "/opt/shortcf"


class ProvisionError(RuntimeError):
    pass


def _b64(s: str) -> str:
    return base64.b64encode(s.encode("utf-8")).decode("ascii")


async def _sh(node, cmd: str, *, input: Optional[str] = None, timeout: int = 60) -> str:
    """Выполнить команду на ноде, вернуть stdout; на ненулевом коде — ProvisionError."""
    r = await _run_on_node(node, cmd, input=input, timeout=timeout)
    out = (r.stdout or "").strip()
    err = (r.stderr or "").strip()
    if r.exit_status != 0:
        raise ProvisionError(f"`{cmd[:60]}` → exit {r.exit_status}: {(err or out)[:300]}")
    return out


async def _write_file(node, path: str, content: str, *, mode: str = "644") -> None:
    await _sh(node, f"base64 -d > {path} && chmod {mode} {path}", input=_b64(content))


async def provision(node, domain_row, cf_token: str, secret_b64: str) -> tuple[bool, list[str], str]:
    """Полный провижн. → (ok, log, error). domain_row.* (cert/record id) НЕ пишем здесь."""
    log: list[str] = []
    dom = domain_row.domain
    key_path = f"{CONF_DIR}/{dom}.key"
    cert_path = f"{CONF_DIR}/{dom}.pem"

    try:
        # 1. зависимости и каталоги
        await _sh(node,
                  "command -v openssl >/dev/null || (apt-get update -y && apt-get install -y openssl); "
                  "command -v nft >/dev/null || (apt-get update -y && apt-get install -y nftables); "
                  f"mkdir -p {CONF_DIR} {APP_DIR} /var/lib/shortcf && chmod 700 {CONF_DIR}",
                  timeout=180)
        log.append("✅ зависимости (openssl/nft) и каталоги")

        # 2. ключ + CSR на ноде (приватный ключ не покидает сервер; CSR в stdout)
        csr = await _sh(node,
                        f"openssl req -new -newkey rsa:2048 -nodes -keyout {key_path} "
                        f"-subj '/CN={dom}' -addext 'subjectAltName=DNS:{dom}' 2>/dev/null "
                        f"&& chmod 600 {key_path}")
        if "BEGIN CERTIFICATE REQUEST" not in csr:
            raise ProvisionError("CSR не сгенерирован")
        log.append("✅ приватный ключ + CSR на ноде")

        # 3. origin-сертификат из CF по CSR; при отказе — self-signed + SSL Full
        strict = True
        try:
            cert_pem, cert_id = await cf.create_origin_cert(cf_token, [dom], csr)
            await _write_file(node, cert_path, cert_pem, mode="644")
            domain_row.cf_cert_id = cert_id
            log.append("✅ origin-сертификат CF установлен")
        except Exception as e:
            strict = False
            await _sh(node,
                      f"openssl req -x509 -key {key_path} -days 3650 -subj '/CN={dom}' "
                      f"-addext 'subjectAltName=DNS:{dom}' -out {cert_path} && chmod 644 {cert_path}")
            log.append(f"⚠️ origin-cert CF недоступен ({str(e)[:80]}) → self-signed, SSL останется Full")

        # 4. конфиг домена + исходник сервиса + unit
        import json as _json
        conf = _json.dumps({"domain": dom, "secret": secret_b64, "cert": cert_path, "key": key_path})
        await _write_file(node, f"{CONF_DIR}/{dom}.json", conf, mode="600")
        await _write_file(node, f"{APP_DIR}/server.py", REDIRECT_SERVICE_SRC, mode="755")
        await _write_file(node, "/etc/systemd/system/shortcf.service", SYSTEMD_UNIT, mode="644")
        await _sh(node, "systemctl daemon-reload && systemctl enable shortcf >/dev/null 2>&1; "
                        "systemctl restart shortcf", timeout=40)
        log.append("✅ мини-сервис shortcf (systemd) запущен")

        # 5. фаервол: 80/443 только с IP Cloudflare (SSH не трогаем)
        v4, v6 = await cf.cf_ip_ranges()
        nft = _nft_ruleset(v4, v6)
        await _write_file(node, f"{CONF_DIR}/nftables.conf", nft, mode="644")
        await _sh(node, f"nft -f {CONF_DIR}/nftables.conf")
        await _write_file(node, "/etc/systemd/system/shortcf-fw.service", _FW_UNIT, mode="644")
        await _sh(node, "systemctl daemon-reload && systemctl enable shortcf-fw >/dev/null 2>&1 || true")
        log.append(f"✅ фаервол CF-only ({len(v4)} v4 / {len(v6)} v6)")

        # 6. локальная проверка 301: регистрируем проверочный код и дергаем его по SNI на 127.0.0.1
        from bot.services.short_domain import register_link
        health = await _sh(node,
                           f"sleep 1; curl -sk --connect-timeout 5 --max-time 10 -o /dev/null "
                           f"-w '%{{http_code}}' --resolve {dom}:443:127.0.0.1 https://{dom}/healthz")
        pcode = await register_link(node, dom, "https://example.org/probe")
        if not pcode:
            raise ProvisionError("не удалось зарегистрировать проверочный код на ноде")
        loc = await _sh(node,
                        f"curl -sk --connect-timeout 5 --max-time 10 -D - -o /dev/null "
                        f"--resolve {dom}:443:127.0.0.1 https://{dom}/{pcode} | grep -i '^location:' || true")
        if health != "200" or "example.org/probe" not in loc.lower():
            raise ProvisionError(f"проверка редиректа не прошла (health={health}, loc={loc[:120]})")
        log.append("✅ редирект 301 проверен локально")

        # 7. SSL до strict, если встал origin-cert
        if strict and domain_row.cf_zone_id:
            try:
                await cf.set_ssl(cf_token, domain_row.cf_zone_id, "strict")
                log.append("✅ SSL/TLS = Full (strict)")
            except Exception as e:
                log.append(f"⚠️ не удалось поднять SSL до strict: {str(e)[:80]}")

        return True, log, ""
    except Exception as e:
        return False, log, str(e)


def _nft_ruleset(v4: list[str], v6: list[str]) -> str:
    e4 = ", ".join(v4) if v4 else ""
    e6 = ", ".join(v6) if v6 else ""
    return f"""#!/usr/sbin/nft -f
# shortcf: пускаем 80/443 только с IP Cloudflare; SSH и прочее не трогаем.
table inet shortcf
delete table inet shortcf
table inet shortcf {{
    set cf4 {{ type ipv4_addr; flags interval; {('elements = { %s }' % e4) if e4 else ''} }}
    set cf6 {{ type ipv6_addr; flags interval; {('elements = { %s }' % e6) if e6 else ''} }}
    chain input {{
        type filter hook input priority -10; policy accept;
        iif "lo" accept
        ct state established,related accept
        tcp dport {{ 80, 443 }} ip saddr @cf4 accept
        tcp dport {{ 80, 443 }} ip6 saddr @cf6 accept
        tcp dport {{ 80, 443 }} drop
    }}
}}
"""


_FW_UNIT = f"""[Unit]
Description=shortcf firewall (Cloudflare-only 80/443)
After=network.target

[Service]
Type=oneshot
ExecStart=/usr/sbin/nft -f {CONF_DIR}/nftables.conf
RemainAfterExit=yes

[Install]
WantedBy=multi-user.target
"""
