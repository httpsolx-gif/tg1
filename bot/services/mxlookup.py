"""Определение провайдера почты по MX-записям (п.52).

Для корпоративных/кастомных доменов (не распознанных напрямую) смотрим MX через
`dig` и классифицируем реального провайдера: Google Workspace, Microsoft 365,
Yahoo, United-Internet (web.de/gmx), Mail.ru, Yandex, Apple, Proton, Zoho.
Результат кэшируется (память + таблица mx_cache), DNS дёргается один раз на домен.

Рубрика (п.115): публичные домены — рубрика провайдера (gmail/hotmail/icloud/yahoo),
локальные бесплатные почты → main; свой/корп домен по MX — только Google/Microsoft/
Apple/Yahoo → их рубрика, прочий хостинг, шлюзы и нераспознанное → None (→ other).
Чистый модуль (sqlmodel + common + subprocess, без aiogram).
"""
from __future__ import annotations

import subprocess
from datetime import datetime

from sqlmodel import Session

from common.db import get_engine
from common.models.mx_cache import MxCache

_engine = get_engine()
_MEM: dict[str, str] = {}   # domain -> provider ("" = нераспознан)

# крупные потребительские домены — провайдер известен без DNS
STATIC_DOMAIN_PROVIDER = {
    "gmail.com": "google", "googlemail.com": "google",
    "outlook.com": "microsoft", "hotmail.com": "microsoft", "live.com": "microsoft",
    "msn.com": "microsoft", "outlook.de": "microsoft", "hotmail.de": "microsoft",
    "yahoo.com": "yahoo", "yahoo.de": "yahoo", "yahoo.co.uk": "yahoo", "yahoo.fr": "yahoo",
    "web.de": "unitedinternet", "gmx.de": "unitedinternet", "gmx.net": "unitedinternet",
    "gmx.com": "unitedinternet", "t-online.de": "unitedinternet",
    "mail.ru": "mailru", "yandex.ru": "yandex",
    "icloud.com": "apple", "me.com": "apple", "mac.com": "apple",
    "aol.com": "yahoo", "aol.de": "yahoo", "ymail.com": "yahoo",
    "proton.me": "proton", "protonmail.com": "proton",
}

# подстрока в MX-хосте → провайдер (порядок важен: шлюзы ловим как "gateway")
_MX_PATTERNS = [
    ("google.com", "google"), ("googlemail.com", "google"), ("aspmx", "google"),
    ("protection.outlook.com", "microsoft"), ("outlook.com", "microsoft"),
    ("office365", "microsoft"), ("hotmail.com", "microsoft"), ("microsoft", "microsoft"),
    ("yahoodns", "yahoo"), ("yahoo.com", "yahoo"),
    # emig.* — приём потребительской почты GMX/WEB.DE (fun-домены gmx-topmail, wolke7…);
    # просто kundenserver/1und1 — хостинг IONOS для своих доменов (корп → other)
    ("emig.", "unitedinternet"), ("gmx.net", "unitedinternet"), ("web.de", "unitedinternet"),
    ("kundenserver", "ionos"), ("1und1", "ionos"), ("einsundeins", "ionos"),
    ("united-internet", "ionos"), ("ui-dns", "ionos"),
    ("freenet.de", "localfree"), ("vodafonemail", "localfree"), ("t-online.de", "localfree"),
    ("dg-sys.net", "localfree"), ("simplelogin", "localfree"), ("snafu.de", "localfree"),
    ("mail.ru", "mailru"),
    ("yandex", "yandex"),
    ("protonmail", "proton"), ("proton.me", "proton"),
    ("icloud.com", "apple"), ("mail.me.com", "apple"), ("apple.com", "apple"),
    ("zoho", "zoho"),
    # шлюзы безопасности — реальный провайдер скрыт, считаем нераспознанным
    ("pphosted", "gateway"), ("proofpoint", "gateway"), ("mimecast", "gateway"),
    ("barracuda", "gateway"), ("messagelabs", "gateway"), ("securence", "gateway"),
    ("trendmicro", "gateway"), ("secureserver.net", "gateway"),
]

PROVIDER_MAILBOX = {
    "google": "gmail", "microsoft": "hotmail", "yahoo": "yahoo",
    "unitedinternet": "main", "mailru": "main", "yandex": "main",
    "apple": "main", "proton": "main", "zoho": "main",
}
# Рубрика по провайдеру (п.115):
#  • публичный домен провайдера (gmail.com, aol.com, posteo.de…) → рубрика провайдера,
#    малоизвестные локальные бесплатные почты → main;
#  • свой/корп домен — по MX: только крупные (Google/Microsoft/Apple/Yahoo) → их рубрика,
#    остальной хостинг (IONOS, Zoho, свой сервер, шлюзы…) → None → other.
PROVIDER_RUBRIC = {"google": "gmail", "microsoft": "hotmail", "apple": "icloud", "yahoo": "yahoo",
                   # по MX видно, что это потребительский почтовик (fun-домены GMX, freenet, vodafone…)
                   "unitedinternet": "main", "mailru": "main", "localfree": "main"}

# метка домена → провайдер, для любого TLD (hotmail.fr, live.de, yahoo.co.jp, aol.de…)
_LABEL_PROVIDER = {
    "gmail": "google", "googlemail": "google",
    "hotmail": "microsoft", "outlook": "microsoft", "live": "microsoft", "msn": "microsoft",
    "windowslive": "microsoft", "passport": "microsoft",
    "yahoo": "yahoo", "ymail": "yahoo", "rocketmail": "yahoo", "aol": "yahoo", "aim": "yahoo",
    "verizon": "yahoo", "icloud": "apple",
}
_EXACT_PROVIDER = {"me.com": "apple", "mac.com": "apple", "aol.com": "yahoo"}

from common.constants import LOCAL_FREEMAIL  # noqa: E402 (п.115/116)


def public_provider(domain: str) -> str | None:
    """Публичный домен почтовика → провайдер ('local' — малоизвестные/локальные). None — свой/корп домен."""
    dom = (domain or "").strip().lower().strip(".")
    if dom in _EXACT_PROVIDER:
        return _EXACT_PROVIDER[dom]
    if dom in LOCAL_FREEMAIL:
        return "local"
    first = dom.split(".", 1)[0]
    return _LABEL_PROVIDER.get(first)


def _resolve_mx(domain: str) -> list[str] | None:
    """MX-хосты домена через dig. None — если dig не отработал (не кэшируем)."""
    try:
        r = subprocess.run(
            ["dig", "+short", "+time=3", "+tries=1", "MX", domain],
            capture_output=True, text=True, timeout=6,
        )
    except Exception:
        return None
    if r.returncode != 0:
        return None
    hosts = []
    for ln in r.stdout.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        hosts.append(ln.split()[-1].rstrip(".").lower())
    return hosts


def _classify(hosts: list[str]) -> str:
    for h in hosts:
        for sub, prov in _MX_PATTERNS:
            if sub in h:
                return "" if prov == "gateway" else prov
    return ""


def provider_for_domain(domain: str) -> str | None:
    """Провайдер домена (google/microsoft/…) или None, если не распознан.
    Кэш: память → mx_cache → dig. "" в кэше = проверен, не распознан."""
    dom = (domain or "").strip().lower().strip(".")
    if not dom or "." not in dom:
        return None
    if dom in STATIC_DOMAIN_PROVIDER:
        return STATIC_DOMAIN_PROVIDER[dom]
    if dom in _MEM:
        return _MEM[dom] or None
    # БД-кэш
    try:
        with Session(_engine) as db:
            row = db.get(MxCache, dom)
            if row is not None:
                _MEM[dom] = row.provider or ""
                return row.provider or None
    except Exception:
        pass
    # DNS
    hosts = _resolve_mx(dom)
    if hosts is None:
        return None  # dig не отработал — не кэшируем, попробуем позже
    prov = _classify(hosts)
    _MEM[dom] = prov
    try:
        with Session(_engine) as db:
            if not db.get(MxCache, dom):
                db.add(MxCache(domain=dom, provider=prov, checked_at=datetime.utcnow().isoformat()))
                db.commit()
    except Exception:
        pass
    return prov or None


def mailbox_for_domain(domain: str) -> str | None:
    p = provider_for_domain(domain)
    return PROVIDER_MAILBOX.get(p) if p else None


def rubric_name_for_domain(domain: str) -> str | None:
    """Имя рубрики для домена получателя, не найденного в «Почтах» рубрик; None → other."""
    pub = public_provider(domain)
    if pub:
        return PROVIDER_RUBRIC.get(pub, "main")
    p = provider_for_domain(domain)  # свой домен — по MX
    return PROVIDER_RUBRIC.get(p) if p else None
