"""SMTP/SHORT по областям (страна × сервис × рубрика, ALL = '*'/0).

Из подходящих областей побеждает настроенная последней.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, update
from sqlmodel import Session, select

from common.db import get_engine
from common.models.aws import AwsAccount
from common.models.scope import ScopeCfg, ScopeSmtp, ScopeAws
from common.models.smtp import SmtpAccount

_engine = get_engine()


def now_iso() -> str:
    return datetime.utcnow().isoformat()


def get_or_create(db: Session, country: str, service: str, rubric_id: int) -> ScopeCfg:
    row = db.exec(
        select(ScopeCfg).where(
            (ScopeCfg.country == country) & (ScopeCfg.service == service) & (ScopeCfg.rubric_id == rubric_id)
        )
    ).first()
    if row is None:
        row = ScopeCfg(country=country, service=service, rubric_id=rubric_id)
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def label(cfg: ScopeCfg, rubric_name: Optional[str] = None) -> str:
    c = "ALL" if cfg.country == "*" else cfg.country
    s = "ALL" if cfg.service == "*" else cfg.service
    r = "ALL" if not cfg.rubric_id else (rubric_name or f"#{cfg.rubric_id}")
    return f"{c} · {s} · {r}"


def base_accounts(db: Session, scope_id: int) -> list[SmtpAccount]:
    ids = db.exec(select(ScopeSmtp.smtp_id).where(ScopeSmtp.scope_id == scope_id)).all()
    if not ids:
        return []
    return list(db.exec(select(SmtpAccount).where(SmtpAccount.id.in_(ids)).order_by(SmtpAccount.id)).all())


def base_aws(db: Session, scope_id: int) -> list[AwsAccount]:
    ids = db.exec(select(ScopeAws.aws_id).where(ScopeAws.scope_id == scope_id)).all()
    if not ids:
        return []
    return list(db.exec(select(AwsAccount).where(AwsAccount.id.in_(ids)).order_by(AwsAccount.id)).all())


def _matching(db: Session, country: Optional[str], service: Optional[str], rubric_id: Optional[int]) -> list[ScopeCfg]:
    return list(db.exec(
        select(ScopeCfg).where(
            ScopeCfg.country.in_(["*", country or "*"])
            & ScopeCfg.service.in_(["*", service or "*"])
            & ScopeCfg.rubric_id.in_([0, rubric_id or 0])
        )
    ).all())


def short_winner(db: Session, country, service, rubric_id) -> Optional[ScopeCfg]:
    rows = [r for r in _matching(db, country, service, rubric_id) if r.short and r.short_at]
    return max(rows, key=lambda r: r.short_at) if rows else None


def scope_short(country, service, rubric_id) -> Optional[bool]:
    """True/False — сокращать или нет по области; None — областей с SHORT нет."""
    with Session(_engine) as db:
        w = short_winner(db, country, service, rubric_id)
        return None if w is None else w.short != "off"


def scope_short_choice(country, service, rubric_id) -> Optional[str]:
    """Что настроено на область: 'off' | 'n9.cl' | 'dom:{id}' | None (не задано)."""
    with Session(_engine) as db:
        w = short_winner(db, country, service, rubric_id)
        return None if w is None else (w.short or None)


def entries(db: Session, scope_id: int) -> list[tuple[str, object]]:
    """Одна база области: SMTP и AWS вместе, порядок — SMTP, затем AWS (по id)."""
    return [("smtp", a) for a in base_accounts(db, scope_id)] + [("aws", a) for a in base_aws(db, scope_id)]


def key(kind: str, acc) -> str:
    return f"{kind}:{acc.id}"


def acc_email(kind: str, acc) -> str:
    return acc.email if kind == "smtp" else acc.email_from


def base_winner(db: Session, country, service, rubric_id) -> Optional[ScopeCfg]:
    rows = [r for r in _matching(db, country, service, rubric_id) if r.smtp_at and entries(db, r.id)]
    return max(rows, key=lambda r: r.smtp_at) if rows else None


def limit_for(acc, cfg: Optional[ScopeCfg] = None) -> int:
    """Лимит аккаунта в области: лимит автосмены области, иначе собственный лимит аккаунта (0 = ∞)."""
    return (cfg.switch_limit if cfg and cfg.switch_limit else 0) or (acc.send_limit or 0)


def _exhausted(count: int, acc, cfg: Optional[ScopeCfg] = None) -> bool:
    """Исчерпан ли аккаунт по счётчику ЭТОЙ области (count), а не по глобальному."""
    lim = limit_for(acc, cfg)
    return lim > 0 and (count or 0) >= lim


# ---- счётчики автосмены на связке область↔аккаунт (раздельно по областям) ----
def _link(db: Session, scope_id: int, kind: str, acc_id: int):
    model, col = (ScopeSmtp, ScopeSmtp.smtp_id) if kind == "smtp" else (ScopeAws, ScopeAws.aws_id)
    return db.exec(select(model).where((model.scope_id == scope_id) & (col == acc_id))).first()


def scope_counts(db: Session, scope_id: int) -> dict:
    """{'smtp:ID'|'aws:ID': sent_count} — счётчики аккаунтов базы в этой области."""
    out: dict = {}
    for l in db.exec(select(ScopeSmtp).where(ScopeSmtp.scope_id == scope_id)).all():
        out[f"smtp:{l.smtp_id}"] = l.sent_count or 0
    for l in db.exec(select(ScopeAws).where(ScopeAws.scope_id == scope_id)).all():
        out[f"aws:{l.aws_id}"] = l.sent_count or 0
    return out


def scope_count(scope_id: int, kind: str, acc_id: int) -> int:
    with Session(_engine) as db:
        l = _link(db, scope_id, kind, acc_id)
        return (l.sent_count or 0) if l else 0


def bump_scope_count(scope_id: int, kind: str, acc_id: int) -> None:
    """+1 к счётчику автосмены аккаунта в этой области (после успешной отправки)."""
    # атомарно в БД: read-modify-write терял инкременты при параллельных отправках (бот + API)
    model = ScopeAws if kind == "aws" else ScopeSmtp
    col = model.aws_id if kind == "aws" else model.smtp_id
    with Session(_engine) as db:
        db.exec(update(model).where(model.scope_id == scope_id, col == acc_id)
                .values(sent_count=func.coalesce(model.sent_count, 0) + 1))
        db.commit()


def reset_scope_count(scope_id: int, kind: str, acc_id: int) -> None:
    with Session(_engine) as db:
        l = _link(db, scope_id, kind, acc_id)
        if l:
            l.sent_count = 0
            db.add(l)
            db.commit()


def current_entry(db: Session, cfg: ScopeCfg, *, advance: bool = False) -> Optional[tuple[str, object]]:
    """Текущий аккаунт базы (SMTP или AWS); недоступен (или advance=принуд. смена) — следующий по кругу.

    Автосмена выкл → лимит не уводит с текущего (только выключенный аккаунт или принуд. смена).
    «По кругу» (cfg.wrap): по лимитам никого не осталось → сбрасываем счётчики базы и снова с первого.
    Счётчики исчерпания — ПО ЭТОЙ области (scope_counts), не глобальные.
    """
    ents = entries(db, cfg.id)
    if not ents:
        return None
    counts = scope_counts(db, cfg.id)
    keys = [key(k, a) for k, a in ents]
    had_current = cfg.current_key in keys
    start = keys.index(cfg.current_key) if had_current else 0
    order = ents[start:] + ents[:start]
    # Принуд. смена уводит с ТЕКУЩЕГО. Если текущего нет (новая база/сброс),
    # нечего пропускать — берём первый доступный, а не второй.
    if advance and had_current:
        order = order[1:]
    check_limit = cfg.autoswitch or advance
    pick = next(((k, a) for k, a in order
                 if a.is_active and not (check_limit and _exhausted(counts.get(key(k, a), 0), a, cfg))), None)
    # По кругу: по лимитам никого → сброс счётчиков активных аккаунтов и снова с первого (не стоп)
    if pick is None and cfg.wrap and check_limit:
        active = [(k, a) for k, a in ents if a.is_active]
        if active:
            for k, a in active:
                l = _link(db, cfg.id, k, a.id)
                if l:
                    l.sent_count = 0
                    db.add(l)
            # принуд. смена: после сброса всё равно уходим с текущего (если есть другой)
            others = [p for p in active if key(*p) != cfg.current_key] if advance else []
            pick = (others or active)[0]
            cfg.current_key = key(*pick)
            db.add(cfg)
            db.commit()
            db.refresh(pick[1])
            return pick
    if pick is not None and key(*pick) != cfg.current_key:
        cfg.current_key = key(*pick)
        db.add(cfg)
        db.commit()
        db.refresh(pick[1])  # commit экспайрит объекты — подгружаем, чтобы работать вне сессии
    return pick


def scope_pick(country, service, rubric_id) -> tuple[bool, Optional[tuple[str, object]], Optional[int]]:
    """(есть ли база, (kind, аккаунт), scope_id). (False, None, None) → брать обычный pick_smtp.

    scope_id нужен, чтобы после отправки инкрементить счётчик именно этой области.
    """
    with Session(_engine) as db:
        w = base_winner(db, country, service, rubric_id)
        if w is None:
            return False, None, None
        return True, current_entry(db, w), w.id
