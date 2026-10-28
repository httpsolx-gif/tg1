"""Общее ядро отправки письма (п.46).

Две чистые (без aiogram-объектов) функции, которыми пользуются и бот-флоу
(`flow_send`), и API-воркер (`api/worker.py`):

- `build_payload(...)` — по стране/сервису/почте/ссылке собирает payload: рубрика
  (по домену, либо «black»), почта, конфиг сервиса, шаблон, отправитель, тема,
  провайдер сокращения, группа SMTP. Бросает `SendError` с готовым текстом, если
  домен без рубрики или домен в дроп-листе black.
- `send_payload(...)` — выбирает аккаунт (база области / фолбэк-пул), шлёт через
  ноду/локально/SES, ведёт счётчик автосмены и пишет `SendLog` (вкл. привязку к
  API-токену и клиентские id/username). Возвращает dict с результатом.

Логика идентична прежнему `flow_send._do_send`/`take_email` — вынесена сюда без
изменения поведения бота (бот теперь зовёт эти функции).
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from common.db import get_engine
from common.models.send_log import SendLog
from common.models.node import Node
from common.models.aws import AwsAccount
from common.models.smtp import SmtpAccount
from bot.services.smtp_pool import pick_smtp
from bot.services.templates import render_template, default_template, resolve_template_html
from bot.services.short_client import shorten_url
from bot.services import mailer as mailer_svc
from bot.services.node_runner import send_via_node, send_ses_via_node
from bot.services.aws_ses import send_via_ses

log = logging.getLogger("bot")
_engine = get_engine()


class SendError(Exception):
    """Отправку нельзя собрать (нет рубрики, домен в дроп-листе black и т.п.).
    Текст — готовое сообщение пользователю/клиенту."""


# ----------------------------- маршрутные помощники -----------------------------
def short_enabled(service: Optional[str], country: Optional[str], target: str) -> bool:
    """Сокращать ли ссылку по легаси-привязкам раздела SHORT (сервис>страна>target>global)."""
    from bot.services.short_policy import get_binding
    with Session(_engine) as db:
        return get_binding(db, service=service, country=country, target=target) is not None


def node_for_aws(aws) -> "Node | None":
    """Нода для SES: активная нода с совпадающим регионом, затем явная привязка aws.node_id."""
    reg = (aws.region or "").strip().lower()
    with Session(_engine) as db:
        nodes = db.exec(
            select(Node).where(Node.is_active == True).order_by(Node.error_count, Node.id)  # noqa: E712
        ).all()
    if reg:
        for n in nodes:
            if (n.region or "").strip().lower() == reg:
                return n
    if aws.node_id:
        for n in nodes:
            if n.id == aws.node_id:
                return n
    return None


def first_node() -> "Node | None":
    """Нода для SMTP: первая активная (по id)."""
    with Session(_engine) as db:
        return db.exec(select(Node).where(Node.is_active == True).order_by(Node.id)).first()  # noqa: E712


async def resolve_short(link: str, provider: str, track: Optional[str] = None) -> str:
    """Короткая ссылка по провайдеру области: off → как есть, dom:{id} → личный домен
    (локально, с трек-id для статистики), иначе → n9."""
    if not link or not provider or provider == "off":
        return link
    if provider.startswith("dom:"):
        from bot.services.short_domain import personal_short
        return await personal_short(provider, link, track)
    return await shorten_url(link)


def _country_date(country: str | None, fmt: str = "%d.%m.%Y") -> str:
    """Текущая дата в поясе страны получателя (IANA-зона из COUNTRY_TZ).

    DST (переход на летнее/зимнее время) учитывается автоматически через zoneinfo,
    поэтому дата не сбивается. Нет зоны/zoneinfo недоступен → локальная дата сервера.
    """
    from common.constants import COUNTRY_TZ
    tzname = COUNTRY_TZ.get((country or "").upper())
    if tzname:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tzname)).strftime(fmt)
        except Exception as e:
            log.warning("tz %s unavailable (%s) — локальная дата", tzname, e)
    return datetime.now().strftime(fmt)


# ----------------------------- сборка payload -----------------------------
def build_payload(
    *,
    country: Optional[str],
    service: str,
    email: str,
    link: str,
    sender_class: str = "white",
    extra: Optional[dict] = None,
) -> dict:
    """Собрать payload отправки (как делал `take_email`). sender_class: white|black
    (ban отсекается выше по стеку). `extra` — «сырые» плейсхолдеры шаблона
    ({'*PRICE*': .., '*PNAME*': ..}). Бросает SendError, если отправку нельзя собрать."""
    from bot.handlers.admin.rubrics import (
        rubric_for_email, rubric_group, ensure_black_rubric, black_denied, service_rubric_cfg,
    )
    from bot.services.mailboxes import mailbox_for_email
    from bot.services.scope import scope_short_choice

    if sender_class == "black":
        if black_denied(email):
            dom = email.rsplit("@", 1)[-1]
            raise SendError(f"🚫 На домен <b>{dom}</b> отправка для black запрещена (дроп-лист рубрики «black»).")
        rub = ensure_black_rubric()
    else:
        rub = rubric_for_email(email)
        if not rub:
            dom = email.rsplit("@", 1)[-1]
            raise SendError(
                f"🚫 Нет рубрики для домена <b>{dom}</b>. Добавь его в «✉️ Почты» нужной рубрики "
                "или создай рубрику <b>other</b> (🧩 SRVS → Рубрики).")

    code = (rub["name"][:1] or "").lower()
    mailbox = mailbox_for_email(email, default="main")
    cfg = service_rubric_cfg(service, rub["id"], mailbox) or {}

    tpl_spec = resolve_template_html(service, code)
    tpl_gen = resolve_template_html(service, "") if tpl_spec is None else None
    if cfg.get("template_html"):
        tpl_label = "(из конфига сервиса)"
    elif tpl_spec is not None:
        tpl_label = f"<code>{service}-{code}.html</code>"
    elif tpl_gen is not None:
        tpl_label = f"<code>{service}.html</code> (общий)"
    else:
        tpl_label = f"<code>{service}-{code}.html</code> ⚠️ (нет — будет стандартный)"
    html = cfg.get("template_html") or tpl_spec or tpl_gen or default_template()

    group = rubric_group(rub)
    provider = scope_short_choice(country, service, rub["id"])
    if provider is None:
        provider = "n9.cl" if short_enabled(service, country, group) else "off"

    return {
        "group": group,
        "country": country,
        "service": service,
        "sender": cfg.get("sender_name") or rub.get("sender_name"),
        "subject": cfg.get("title") or rub.get("title") or "Notification",
        "template": html,
        "link": link,
        "email": email,
        "short_provider": provider,
        "mailbox": mailbox,
        "rubric_id": rub["id"],
        "rubric_name": f"{rub['name']} · {mailbox}",
        "extra": extra or None,
        # подсказки для превью в боте (send_payload их игнорирует)
        "_preview": {
            "rub_name": rub["name"],
            "tpl_label": tpl_label,
            "ovr": " 🔧" if cfg else "",
            "title_disp": cfg.get("title") or rub.get("title") or "—",
            "sender_disp": cfg.get("sender_name") or rub.get("sender_name") or "—",
        },
    }


# ----------------------------- собственно отправка -----------------------------
async def send_payload(
    payload: dict,
    *,
    user_id: Optional[int] = None,
    api_token_id: Optional[int] = None,
    ext_id: Optional[str] = None,
    ext_username: Optional[str] = None,
) -> dict:
    """Выполнить отправку по payload. Пишет SendLog. Возвращает
    {ok, info, short, from_disp, via}. Исключений наружу не бросает (ошибки транспорта
    отражаются в ok/info)."""
    from bot.services.scope import scope_pick, bump_scope_count

    provider = payload.get("short_provider") or "off"
    track = secrets.token_hex(6) if provider.startswith("dom:") else None
    short = await resolve_short(payload["link"], provider, track)
    # плейсхолдеры: от клиента (*PRICE*/*PNAME* в payload["extra"]) + системные.
    # *DATE* — «чисто наше»: текущая дата в поясе страны получателя (DST учитывается).
    raw = dict(payload.get("extra") or {})
    raw["*DATE*"] = _country_date(payload.get("country"))
    html = render_template(payload["template"], raw=raw,
                           title=payload["subject"], url=short)

    smtp = None
    from_disp = None
    via = "локально"
    has_base, pick, scope_id = scope_pick(payload.get("country"), payload.get("service"), payload.get("rubric_id"))
    if not has_base:
        smtp = await pick_smtp(
            group=payload["group"], country_code=payload.get("country"), service=payload.get("service"))
        pick = ("smtp", smtp) if smtp else None
    if pick is None:
        if payload["group"] == "black":
            where = ("в базе области (все исчерпаны/выключены)" if has_base
                     else "(нет SMTP для black — задайте в 🧩 Сервисы → рубрика «black» → SMTP)")
        else:
            where = ("в базе области (все исчерпаны/выключены)" if has_base
                     else f"(базы области нет, в разделе SMTP для группы «{payload['group']}» тоже)")
        return {"ok": False, "no_account": True, "info": where,
                "short": short, "from_disp": None, "via": via}
    kind, acc = pick

    if kind == "aws":
        from_disp = acc.email_from
        node = node_for_aws(acc)
        if node is not None:
            via = f"SES через ноду {node.login}@{node.ip} ({acc.region})"
            ok, info = await send_ses_via_node(node, acc, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))
        else:
            via = f"SES {acc.region}, с бота — нет ноды с этим регионом"
            ok, info = await send_via_ses(acc, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))
    else:
        smtp = acc
        from_disp = smtp.email
        node = first_node()
        if node is not None:
            via = f"SMTP через ноду {node.login}@{node.ip}"
            ok, info = await send_via_node(node, smtp, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))
        else:
            via = "SMTP локально (активных нод нет)"
            ok, info = await mailer_svc.send_via_account(smtp, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))

    if ok:
        try:
            if has_base and scope_id:
                bump_scope_count(scope_id, kind, acc.id)
            elif acc.id is not None:
                with Session(_engine) as db:
                    row = db.get(SmtpAccount if kind == "smtp" else AwsAccount, acc.id)
                    if row:
                        row.sent_count = (row.sent_count or 0) + 1
                        db.add(row)
                        db.commit()
        except Exception as e:
            log.warning("sent_count: %s", e)

    try:
        with Session(_engine) as db:
            db.add(SendLog(
                user_id=user_id, smtp_id=getattr(smtp, "id", None), email=payload["email"],
                status="ok" if ok else "fail", error=None if ok else (info or "send failed"),
                created_at=datetime.utcnow().isoformat(),
                country=payload.get("country"), service=payload.get("service"),
                mailbox=payload.get("mailbox"), short_provider=provider, track=track,
                api_token_id=api_token_id, ext_id=ext_id, ext_username=ext_username,
            ))
            db.commit()
    except Exception as e:
        log.warning("SendLog: %s", e)

    return {"ok": ok, "info": info, "short": short, "from_disp": from_disp, "via": via}
