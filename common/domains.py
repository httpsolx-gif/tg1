"""Матчинг домена получателя по списку токенов рубрики/почты.

Токены можно задавать как КОРОТКО, так и полным доменом — в одном поле вперемешку:
  - `gmx`      → ловит gmx.de, gmx.net, gmx.com, mail.gmx.de (имя домена перед зоной)
  - `web`      → web.de, web.com
  - `gmail`    → gmail.com  (НЕ googlemail — это другая метка, добавляй отдельно)
  - `gmx.de`   → gmx.de и поддомены (*.gmx.de)  (полный домен/суффикс)
Разделители в поле: пробел/запятая/点с запятой; ведущий `@` игнорируется (`@gmx` == `gmx`).
"""
from __future__ import annotations

import re

_SPLIT = re.compile(r"[\s,;]+")
# вторые уровни двухчастных зон: yahoo.co.uk, mail.com.au, ne.jp…
_SLD2 = {"co", "com", "net", "org", "ac", "gov", "edu", "ne", "or", "go"}


def parse_tokens(s: str) -> list[str]:
    """Разбить строку доменов/токенов в нормализованный список."""
    return [t.strip().lower().lstrip("@") for t in _SPLIT.split(s or "") if t.strip()]


def token_matches(token: str, dom: str) -> bool:
    """Совпадает ли один токен с доменом получателя."""
    token = (token or "").strip().lower().lstrip("@")
    dom = (dom or "").strip().lower()
    if not token or not dom:
        return False
    if "." in token:
        # полный домен или суффикс: gmx.de ловит gmx.de и mail.gmx.de
        return dom == token or dom.endswith("." + token)
    # короткий токен = ИМЯ домена (метка прямо перед зоной): gmx → gmx.de/gmx.net/mail.gmx.de,
    # web → web.de. Метка-поддомен или сама зона не считаются (п.116): mail ≠ mail.kleinanzeigen.de,
    # online ≠ firma.online
    labels = dom.split(".")
    for i, lab in enumerate(labels):
        if lab != token:
            continue
        rest = labels[i + 1:]
        if len(rest) == 1 or (len(rest) == 2 and rest[0] in _SLD2):
            return True
    return False


def domain_in(dom: str, tokens_str: str) -> bool:
    """Домен `dom` подпадает хотя бы под один токен из строки `tokens_str`."""
    dom = (dom or "").strip().lower()
    if not dom:
        return False
    return any(token_matches(t, dom) for t in parse_tokens(tokens_str))
