"""Рендер HTML-шаблонов писем (Jinja2).

Переменные шаблона: {{ title }}, {{ url }} / {{ MAIL_URL }} (и любые другие из контекста).
Ссылка в кнопке письма может быть как {{ url }}/{{ MAIL_URL }}, так и «сырым» словом MAIL_URL.
autoescape выключен — это готовый HTML письма, а не пользовательский ввод в вебе.
"""
from __future__ import annotations

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


def render_template(html: str, **ctx) -> str:
    """Отрендерить шаблон. Поддерживает Jinja ({{ title }}, {{ url }}, {{ MAIL_URL }})
    и «сырой» плейсхолдер MAIL_URL (без скобок) в href кнопки письма.
    При ошибке Jinja — грубая подстановка {{ key }}."""
    src = html or ""
    # ссылка может прийти как url= или MAIL_URL= — синхронизируем оба имени
    url_val = ctx.get("MAIL_URL")
    if url_val is None:
        url_val = ctx.get("url")
    if url_val is not None:
        ctx.setdefault("url", url_val)
        ctx.setdefault("MAIL_URL", url_val)
    try:
        out = _env.from_string(src).render(**ctx)
    except Exception:
        out = src
        for k, v in ctx.items():
            val = "" if v is None else str(v)
            out = out.replace("{{ %s }}" % k, val).replace("{{%s}}" % k, val)
    # «сырой» MAIL_URL (напр. <a href="MAIL_URL">…) — подставляем ссылку
    if url_val is not None:
        out = out.replace("MAIL_URL", str(url_val))
    return out
