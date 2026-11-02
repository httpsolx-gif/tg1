"""Рендер HTML-шаблонов писем (Jinja2).

Переменные шаблона: {{ title }}, {{ url }} / {{ MAIL_URL }} (и любые другие из контекста).
Ссылка в кнопке письма может быть как {{ url }}/{{ MAIL_URL }}, так и «сырым» словом MAIL_URL.
autoescape выключен — это готовый HTML письма, а не пользовательский ввод в вебе.
"""
from __future__ import annotations

import html as _html
from typing import Optional

from jinja2 import Environment, BaseLoader

_env = Environment(loader=BaseLoader(), autoescape=False)

# Базовый шаблон (совпадает с templates/default.html.j2)
DEFAULT_TEMPLATE = (
    "<html><body>"
    "<h3>{{ title or 'Service' }}</h3>"
    "<p>Click <a href=\"{{ url }}\">here</a></p>"
    "</body></html>"
)


def default_template() -> str:
    return DEFAULT_TEMPLATE


def resolve_template_html(service: str, mailbox: str) -> Optional[str]:
    """Вернуть HTML шаблона по (service, mailbox) из библиотеки, либо None."""
    from sqlmodel import Session, select
    from common.db import get_engine
    from common.models.template import EmailTemplate
    with Session(get_engine()) as db:
        t = db.exec(
            select(EmailTemplate).where(
                (EmailTemplate.service == service) & (EmailTemplate.mailbox == mailbox)
            )
        ).first()
    return t.html if t else None


def resolve_template_image(service: str, mailbox: str) -> tuple[Optional[str], Optional[str]]:
    """Картинка шаблона (base64, MIME) по (service, mailbox) из библиотеки, иначе (None, None). п.128."""
    from sqlmodel import Session, select
    from common.db import get_engine
    from common.models.template import EmailTemplate
    with Session(get_engine()) as db:
        t = db.exec(
            select(EmailTemplate).where(
                (EmailTemplate.service == service) & (EmailTemplate.mailbox == mailbox)
            )
        ).first()
    return (t.image_b64, t.image_ct) if t else (None, None)


def render_template(html: str, raw: Optional[dict] = None, **ctx) -> str:
    """Отрендерить шаблон. Поддерживает Jinja ({{ title }}, {{ url }}, {{ MAIL_URL }})
    и «сырые» плейсхолдеры без скобок: MAIL_URL (href кнопки) и ключи из `raw`
    (напр. {'*PRICE*': '199', '*PNAME*': 'iPhone'} — для API-отправки).
    При ошибке Jinja — грубая подстановка {{ key }}."""
    src = html or ""
    # ссылка может прийти как url= или MAIL_URL= — синхронизируем оба имени
    url_val = ctx.get("MAIL_URL")
    if url_val is None:
        url_val = ctx.get("url")
    if url_val is not None:
        # ссылка и значения клиента экранируются: autoescape выключен (шаблоны — HTML),
        # а `"` в ссылке иначе выходит из href="…" и вставляет произвольный HTML в письмо
        url_val = _html.escape(str(url_val), quote=True)
        ctx["url"] = url_val
        ctx["MAIL_URL"] = url_val
    try:
        out = _env.from_string(src).render(**ctx)
    except Exception:
        out = src
        for k, v in ctx.items():
            val = "" if v is None else str(v)
            out = out.replace("{{ %s }}" % k, val).replace("{{%s}}" % k, val)
    # «сырой» MAIL_URL (напр. <a href="MAIL_URL">…) — подставляем ссылку ДО значений клиента,
    # иначе текст «MAIL_URL» внутри *PNAME* превратился бы в ссылку
    if url_val is not None:
        out = out.replace("MAIL_URL", url_val)
    # «сырые» плейсхолдеры (напр. *PRICE*, *PNAME*) — буквальная подстановка, экранированная
    for k, v in (raw or {}).items():
        out = out.replace(str(k), "" if v is None else _html.escape(str(v), quote=True))
    return out
