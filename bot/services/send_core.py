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

import html as _html
import logging
import secrets
from datetime import datetime
from typing import Optional

from sqlalchemy import func, update
from sqlmodel import Session, select

from common.db import get_engine
from common.models.send_log import SendLog
from common.models.aws import AwsAccount
from common.models.smtp import SmtpAccount
from bot.services.smtp_pool import pick_smtp
from bot.services.templates import render_template, default_template, resolve_template_html
from bot.services.short_client import shorten_url
from bot.services import mailer as mailer_svc
from bot.services.node_runner import send_via_node, send_ses_via_node
from bot.services.node_pool import send_nodes, has_send_nodes, ses_nodes, send_with_failover

log = logging.getLogger("bot")
_engine = get_engine()


class SendError(Exception):
    """Отправку нельзя собрать (нет рубрики, домен в дроп-листе black и т.п.).
    Текст — готовое сообщение пользователю/клиенту."""


def _log_send(payload: dict, res: dict, *, user_id, api_token_id, ext_username) -> None:
    """Залогировать результат отправки в каналы (SEND_CHANNEL_ID / ERR_CHANNEL_ID).

    Канал отправок слушает записи с extra is_send=True (фильтр OnlySends) — туда идёт
    КАЖДАЯ попытка (ok и fail). Канал ошибок слушает WARNING+ — туда дополнительно идёт
    только фейл. Так в «отправках» виден весь поток, а в «ошибках» — только сбои
    (вкл. ошибки отправки, как и просили). Файлы sends.jsonl/errors.jsonl — заодно.
    """
    who = (ext_username and f"@{ext_username}") or (f"id{user_id}" if user_id else None) \
        or (f"API#{api_token_id}" if api_token_id else "бот")
    # имя API-токена (label), если отправка пришла по API
    api_name = None
    if api_token_id:
        try:
            from bot.services import api_tokens as _T
            t = _T.get(api_token_id)
            api_name = (t.label if t else None) or f"#{api_token_id}"
        except Exception:
            api_name = f"#{api_token_id}"
    extra = payload.get("extra") or {}
    pname = extra.get("*PNAME*")
    price = extra.get("*PRICE*")
    ok = bool(res.get("ok"))
    via = res.get("via") or "—"
    when = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    svc = f"{payload.get('country') or '—'}/{payload.get('service') or '—'}"
    email = payload.get("email") or "?"
    link = payload.get("link") or "—"
    answer = ("Отправлено" if ok
              else (res.get("info") or "ошибка отправки").replace("\n", " ")[:300])
    # многострочная карточка (plain text — каналы без parse_mode): поля по просьбе юзера.
    # «Узел» = via (какая нода/локально) — виден и на успехе, и на ошибке.
    lines = [
        "✅ Отправка" if ok else "❌ Отправка",
        f"API: {api_name} ({who})" if api_name else f"Кто: {who}",
        f"Время: {when}",
        f"Сервис: {svc}",
        f"Почта: {email}",
        f"Ссылка: {link}",
        f"Товар: {pname if pname is not None else '—'}",
        f"Цена: {price if price is not None else '—'}",
        f"Узел: {via}",
        f"Ответ: {answer}",
    ]
    msg = "\n".join(lines)
    # канал отправок — каждая попытка (ok и fail); канал ошибок (WARNING) — только fail
    log.info(msg, extra={"is_send": True})
    if not ok:
        log.warning(msg)


# ----------------------------- маршрутные помощники -----------------------------
def short_enabled(service: Optional[str], country: Optional[str], target: str) -> bool:
    """Сокращать ли ссылку по легаси-привязкам раздела SHORT (сервис>страна>target>global)."""
    from bot.services.short_policy import get_binding
    with Session(_engine) as db:
        return get_binding(db, service=service, country=country, target=target) is not None


async def resolve_short(link: str, provider: str, track: Optional[str] = None) -> str:
    """Короткая ссылка по провайдеру области: off → как есть, dom:{id} → личный домен
    (локально, с трек-id для статистики), иначе → n9."""
    if not link or not provider or provider == "off":
        return link
    if provider.startswith("dom:"):
        from bot.services.short_domain import personal_short
        return await personal_short(provider, link, track)
    return await shorten_url(link)


def sent_recently(email: str, within_hours: int = 24) -> bool:
    """Была ли УСПЕШНАЯ отправка на этот email за последние within_hours (антидубль).
    Учитываются все ok-отправки (бот+API). Сравнение ISO-дат лексикографическое."""
    from datetime import timedelta
    e = (email or "").strip().lower()
    if not e:
        return False
    cutoff = (datetime.utcnow() - timedelta(hours=within_hours)).isoformat()
    with Session(_engine) as db:
        emails = db.exec(
            select(SendLog.email).where((SendLog.status == "ok") & (SendLog.created_at >= cutoff))
        ).all()
    return any((x or "").strip().lower() == e for x in emails)


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


def resolve_send_template(service: str, code: str, rubric_id, mailbox: str) -> str:
    """Исходный HTML, который уйдёт при отправке этой связки (без уникализации).
    Общая точка для build_payload и генерации уникализации — чтобы сигнатуры совпадали."""
    from bot.handlers.admin.rubrics import service_rubric_cfg
    cfg = service_rubric_cfg(service, rubric_id or 0, mailbox) or {}
    tpl_spec = resolve_template_html(service, code)
    tpl_gen = resolve_template_html(service, "") if tpl_spec is None else None
    return cfg.get("template_html") or tpl_spec or tpl_gen or default_template()


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
    from bot.services.suppression import is_suppressed
    from bot.services.emailfix import fix_email

    # авто-исправление очевидных опечаток домена (wrb.de→web.de, gamil.com→gmail.com)
    orig_email = email
    email = fix_email(email)

    # suppression-лист: на отписки/жалобы/баунсы не слать (бережём репутацию)
    if is_suppressed(email):
        raise SendError("🚫 Адрес в suppression-листе (отписка/жалоба) — отправка отменена.")

    if sender_class == "black":
        if black_denied(email):
            dom = email.rsplit("@", 1)[-1]
            raise SendError(f"🚫 На домен <b>{_html.escape(dom)}</b> отправка для black запрещена (дроп-лист рубрики «black»).")
        rub = ensure_black_rubric()
    else:
        rub = rubric_for_email(email)
        if not rub:
            dom = email.rsplit("@", 1)[-1]
            raise SendError(
                f"🚫 Нет рубрики для домена <b>{_html.escape(dom)}</b>. Добавь его в «✉️ Почты» нужной рубрики "
                "или создай рубрику <b>other</b> (🧩 SRVS → Рубрики).")

    code = (rub["name"][:1] or "").lower()
    mailbox = mailbox_for_email(email, default="main")
    cfg = service_rubric_cfg(service, rub["id"], mailbox) or {}

    tpl_spec = resolve_template_html(service, code)
    tpl_gen = resolve_template_html(service, "") if tpl_spec is None else None
    if cfg.get("template_html"):
        tpl_label = "(из конфига сервиса)"
    elif tpl_spec is not None:
        tpl_label = f"<code>{_html.escape(service)}-{code}.html</code>"
    elif tpl_gen is not None:
        tpl_label = f"<code>{_html.escape(service)}.html</code> (общий)"
    else:
        tpl_label = f"<code>{_html.escape(service)}-{code}.html</code> ⚠️ (нет — будет стандартный)"
    html = cfg.get("template_html") or tpl_spec or tpl_gen or default_template()

    from bot.services import uniq
    ucfg = uniq.parse_cfg(cfg.get("uniq_opts"))
    uniq_html = uniq.active_variant(service, rub["id"], mailbox, html, ucfg) if ucfg.get("on") else None

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
        "uniq_html": uniq_html,
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
            "email_fixed": (orig_email, email) if email != orig_email else None,
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
    job_id: Optional[str] = None,
) -> dict:
    """Выполнить отправку по payload. Пишет SendLog. Возвращает
    {ok, info, short, from_disp, via}. Исключений наружу не бросает: сбой БД/сокращения
    и т.п. → ok=False + запись fail в SendLog (иначе бот висит на «⏳ Отправляю…»,
    а задача API пропадает без следа)."""
    try:
        res = await _send_payload(payload, user_id=user_id, api_token_id=api_token_id,
                                  ext_id=ext_id, ext_username=ext_username, job_id=job_id)
        _log_send(payload, res, user_id=user_id, api_token_id=api_token_id, ext_username=ext_username)
        return res
    except Exception as e:
        log.exception("send_payload: внутренняя ошибка")
        info = f"внутренняя ошибка: {type(e).__name__}: {e}"
        try:
            with Session(_engine) as db:
                db.add(SendLog(
                    user_id=user_id, smtp_id=None, email=payload.get("email"), status="fail",
                    error=info[:500], created_at=datetime.utcnow().isoformat(),
                    country=payload.get("country"), service=payload.get("service"),
                    mailbox=payload.get("mailbox"), short_provider=payload.get("short_provider"),
                    api_token_id=api_token_id, ext_id=ext_id, ext_username=ext_username, job_id=job_id,
                ))
                db.commit()
        except Exception as e2:
            log.warning("SendLog (после ошибки): %s", e2)
        res = {"ok": False, "info": info, "short": payload.get("link"), "from_disp": None, "via": "—"}
        _log_send(payload, res, user_id=user_id, api_token_id=api_token_id, ext_username=ext_username)
        return res


async def _send_payload(
    payload: dict,
    *,
    user_id: Optional[int],
    api_token_id: Optional[int],
    ext_id: Optional[str],
    ext_username: Optional[str],
    job_id: Optional[str],
) -> dict:
    from bot.services.scope import scope_pick, bump_scope_count

    provider = payload.get("short_provider") or "off"
    track = secrets.token_hex(6) if provider.startswith("dom:") else None
    short = await resolve_short(payload["link"], provider, track)
    # плейсхолдеры: от клиента (*PRICE*/*PNAME* в payload["extra"]) + системные.
    # *DATE* — «чисто наше»: текущая дата в поясе страны получателя (DST учитывается).
    raw = dict(payload.get("extra") or {})
    raw["*DATE*"] = _country_date(payload.get("country"))
    tpl = payload.get("uniq_html") or payload["template"]
    html = render_template(tpl, raw=raw, title=payload["subject"], url=short)

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
        # ноды региона: основные → резервные; не ответила → следующая (п.59)
        nodes = ses_nodes(acc)
        if nodes:
            ok, info, node, all_down = await send_with_failover(nodes, lambda n: send_ses_via_node(
                n, acc, payload["email"], payload["subject"], html, sender_name=payload.get("sender")))
            if node is not None:
                via = f"SES через ноду {node.login}@{node.ip} ({acc.region})" + (" · резерв" if node.is_backup else "")
            else:
                via = f"SES {acc.region} — ноды региона недоступны"
        else:
            # только нода своего региона: нет её — не шлём (ни с бота, ни с чужой ноды)
            via = f"SES {acc.region} — нет ноды с этим регионом"
            ok, info = False, f"нет активной ноды отправки с регионом {acc.region} (NODES)"
    else:
        smtp = acc
        from_disp = smtp.email
        # основные ноды → резервные; не ответила → следующая (п.59)
        nodes = send_nodes()
        if nodes:
            ok, info, node, all_down = await send_with_failover(nodes, lambda n: send_via_node(
                n, smtp, payload["email"], payload["subject"], html, sender_name=payload.get("sender")))
            if node is not None:
                via = f"SMTP через ноду {node.login}@{node.ip}" + (" · резерв" if node.is_backup else "")
            else:
                # все ноды лежат — НЕ шлём с IP бота, отдаём ошибку
                via = "SMTP — все ноды недоступны"
        elif has_send_nodes():
            # ноды есть, но все с регионом (они только под SES) — с IP бота не шлём
            via = "SMTP — нет ноды без региона"
            ok, info = False, "нет активной ноды отправки без региона (ноды с регионом — только для SES своего региона)"
        else:
            via = "SMTP локально (активных нод нет)"
            ok, info = await mailer_svc.send_via_account(smtp, payload["email"], payload["subject"], html, sender_name=payload.get("sender"))

    if ok:
        try:
            if has_base and scope_id:
                bump_scope_count(scope_id, kind, acc.id)
            elif acc.id is not None:
                # атомарный +1 (параллельные отправки бота и API не теряют инкременты)
                model = SmtpAccount if kind == "smtp" else AwsAccount
                with Session(_engine) as db:
                    db.exec(update(model).where(model.id == acc.id)
                            .values(sent_count=func.coalesce(model.sent_count, 0) + 1))
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
                job_id=job_id,
            ))
            db.commit()
    except Exception as e:
        log.warning("SendLog: %s", e)

    return {"ok": ok, "info": info, "short": short, "from_disp": from_disp, "via": via}
