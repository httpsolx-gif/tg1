from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlmodel import Session, select, and_

from common.models.short import ShortBinding, ShortScope


# Единственный поддерживаемый сокращатель на данный момент.
DEFAULT_ADAPTER = "n9"


# Priority (most specific → least specific):
# 1) service + target
# 2) service
# 3) country + target
# 4) country
# 5) target
# 6) global (no country, no service, no target)
PRIORITY_MATRIX: Sequence[tuple[Optional[str], Optional[str], Optional[str]]] = (
    # (service, country, target) — most specific → least specific.
    # Сервис в админке выбирается внутри страны, поэтому сервис-привязка
    # хранится вместе со страной — учитываем это при разрешении.
    ("*", "*", "*"),    # service + country + target
    ("*", "*", None),   # service + country
    ("*", None, "*"),   # service + target (без страны)
    ("*", None, None),  # service only
    (None, "*", "*"),   # country + target
    (None, "*", None),  # country
    (None, None, "*"),  # target
    (None, None, None), # global
)


def _scope_for(service: Optional[str], country: Optional[str]) -> str:
    """Вывести scope по заданным полям (target ортогонален и scope не меняет)."""
    if service:
        return ShortScope.service.value
    if country:
        return ShortScope.country.value
    return ShortScope.global_.value


def _pick(
    db: Session,
    *,
    service: Optional[str],
    country: Optional[str],
    target: Optional[str],
) -> Optional[ShortBinding]:
    """
    Return the first enabled ShortBinding that matches the provided scope
    according to PRIORITY_MATRIX. Newest binding wins within the same scope.
    """
    service = service or None
    country = country or None
    target = target or None

    priority_order: list[tuple[Optional[str], Optional[str], Optional[str]]] = []
    for svc_mask, ctry_mask, tgt_mask in PRIORITY_MATRIX:
        svc = service if svc_mask == "*" else (None if svc_mask is None else svc_mask)
        ctry = country if ctry_mask == "*" else (None if ctry_mask is None else ctry_mask)
        tgt = target if tgt_mask == "*" else (None if tgt_mask is None else tgt_mask)
        if (svc_mask == "*" and svc is None) or (ctry_mask == "*" and ctry is None) or (tgt_mask == "*" and tgt is None):
            continue
        priority_order.append((svc, ctry, tgt))

    for svc, ctry, tgt in priority_order:
        stmt = (
            select(ShortBinding)
            .where(
                and_(
                    ShortBinding.enabled.is_(True),
                    ShortBinding.service.is_(svc) if svc is None else ShortBinding.service == svc,
                    ShortBinding.country.is_(ctry) if ctry is None else ShortBinding.country == ctry,
                    ShortBinding.target.is_(tgt) if tgt is None else ShortBinding.target == tgt,
                )
            )
            .order_by(ShortBinding.id.desc())
            .limit(1)
        )
        row = db.exec(stmt).first()
        if row:
            return row
    return None


def resolve_short_adapter(
    db: Session,
    *,
    service: Optional[str] = None,
    country: Optional[str] = None,
    target: Optional[str] = None,
    default_adapter: str = DEFAULT_ADAPTER,
) -> str:
    """
    Decide which shortener adapter to use for given scope.
    Пока поддерживается один адаптер (n9), поэтому возвращаем его при наличии
    включённой привязки и как дефолт.
    """
    row = _pick(db, service=service, country=country, target=target)
    return default_adapter if (row and row.enabled) else default_adapter


def get_binding(
    db: Session,
    *,
    service: Optional[str] = None,
    country: Optional[str] = None,
    target: Optional[str] = None,
) -> Optional[ShortBinding]:
    """
    Public read helper: return the matching enabled ShortBinding for the given
    scope according to PRIORITY_MATRIX (or None if not found).
    """
    return _pick(db, service=service, country=country, target=target)


def set_binding(
    db: Session,
    *,
    adapter: str = DEFAULT_ADAPTER,
    service: Optional[str] = None,
    country: Optional[str] = None,
    target: Optional[str] = None,
    is_active: bool = True,
) -> ShortBinding:
    """
    Backward-compatible alias for set_short_binding so older imports
    (`from bot.services.short_policy import set_binding`) keep working.
    `adapter` принимается для совместимости (на данный момент всегда n9).
    """
    return set_short_binding(
        db,
        adapter=adapter,
        service=service,
        country=country,
        target=target,
        is_active=is_active,
    )


def set_short_binding(
    db: Session,
    *,
    adapter: str = DEFAULT_ADAPTER,
    service: Optional[str] = None,
    country: Optional[str] = None,
    target: Optional[str] = None,
    is_active: bool = True,
) -> ShortBinding:
    """
    Upsert по уникальному ключу (scope, country, service, target).
    Если строка уже есть — обновляем enabled; иначе создаём новую.
    `adapter` на данный момент не хранится (поддерживается только n9), но
    принимается для совместимости с вызовами из админки.
    """
    service = service or None
    country = country or None
    target = target or None
    scope = _scope_for(service, country)

    stmt = select(ShortBinding).where(
        and_(
            ShortBinding.scope == scope,
            ShortBinding.service.is_(service) if service is None else ShortBinding.service == service,
            ShortBinding.country.is_(country) if country is None else ShortBinding.country == country,
            ShortBinding.target.is_(target) if target is None else ShortBinding.target == target,
        )
    )
    existing = db.exec(stmt).first()
    if existing:
        existing.enabled = is_active
        existing.updated_at = datetime.utcnow()
        db.add(existing)
        db.commit()
        db.refresh(existing)
        return existing

    new_row = ShortBinding(
        scope=scope,
        service=service,
        country=country,
        target=target,
        enabled=is_active,
    )
    db.add(new_row)
    db.commit()
    db.refresh(new_row)
    return new_row


def clear_short_bindings(
    db: Session,
    *,
    scope: str = "all",
) -> int:
    """
    Clear bindings by scope:
      - "all": disable everything
      - "unused": keep only the newest per (service,country,target), disable the rest
      - "service:*" / "country:*" / "target:*": disable that level
    Returns number of rows affected.
    """
    filters = [ShortBinding.enabled.is_(True)]

    if scope == "all":
        pass
    elif scope == "unused":
        groups = db.exec(
            select(ShortBinding.service, ShortBinding.country, ShortBinding.target)
            .where(ShortBinding.enabled.is_(True))
            .group_by(ShortBinding.service, ShortBinding.country, ShortBinding.target)
        ).all()
        newest_ids = set()
        for svc, ctry, tgt in groups:
            newest = (
                select(ShortBinding.id)
                .where(
                    and_(
                        ShortBinding.service.is_(svc) if svc is None else ShortBinding.service == svc,
                        ShortBinding.country.is_(ctry) if ctry is None else ShortBinding.country == ctry,
                        ShortBinding.target.is_(tgt) if tgt is None else ShortBinding.target == tgt,
                        ShortBinding.enabled.is_(True),
                    )
                )
                .order_by(ShortBinding.id.desc())
                .limit(1)
            )
            row_id = db.exec(newest).first()
            if row_id:
                newest_ids.add(row_id)
        affected = 0
        for r in db.exec(select(ShortBinding).where(ShortBinding.enabled.is_(True))).all():
            if r.id not in newest_ids:
                r.enabled = False
                r.updated_at = datetime.utcnow()
                db.add(r)
                affected += 1
        db.commit()
        return affected
    elif scope.startswith("service:"):
        filters.append(ShortBinding.service.is_not(None))
    elif scope.startswith("country:"):
        filters.append(ShortBinding.country.is_not(None))
    elif scope.startswith("target:"):
        filters.append(ShortBinding.target.is_not(None))
    else:
        return 0

    rows = db.exec(select(ShortBinding).where(and_(*filters))).all()
    affected = 0
    for r in rows:
        r.enabled = False
        r.updated_at = datetime.utcnow()
        db.add(r)
        affected += 1
    db.commit()
    return affected
