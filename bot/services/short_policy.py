from __future__ import annotations

from typing import Optional, Sequence

from sqlmodel import Session, select, and_

from common.models.short import ShortBinding


# Priority (most specific → least specific):
# 1) service + target
# 2) service
# 3) country + target
# 4) country
# 5) target
# 6) global (no country, no service, no target)
PRIORITY_MATRIX: Sequence[tuple[Optional[str], Optional[str], Optional[str]]] = (
    # (service, country, target)
    ("*", None, "*"),   # any explicit service + explicit target
    ("*", None, None),  # any explicit service
    (None, "*", "*"),   # any explicit country + explicit target
    (None, "*", None),  # any explicit country
    (None, None, "*"),  # any explicit target
    (None, None, None), # global
)


def _pick(
    db: Session,
    *,
    service: Optional[str],
    country: Optional[str],
    target: Optional[str],
) -> Optional[ShortBinding]:
    """
    Return the first active ShortBinding that matches the provided scope
    according to PRIORITY_MATRIX. Newest binding wins within the same scope.
    """
    # Normalize empty strings → None
    service = service or None
    country = country or None
    target = target or None

    # Build concrete priority list for this lookup
    # Replace wildcards with provided values when present, otherwise keep None
    priority_order: list[tuple[Optional[str], Optional[str], Optional[str]]] = []
    for svc_mask, ctry_mask, tgt_mask in PRIORITY_MATRIX:
        svc = service if svc_mask == "*" else (None if svc_mask is None else svc_mask)
        ctry = country if ctry_mask == "*" else (None if ctry_mask is None else ctry_mask)
        tgt = target if tgt_mask == "*" else (None if tgt_mask is None else tgt_mask)
        # If mask asked for "*" but we don't have a concrete value (e.g. target None),
        # this level isn't applicable.
        if (svc_mask == "*" and svc is None) or (ctry_mask == "*" and ctry is None) or (tgt_mask == "*" and tgt is None):
            continue
        priority_order.append((svc, ctry, tgt))

    # Query per level until we find a match
    for svc, ctry, tgt in priority_order:
        stmt = (
            select(ShortBinding)
            .where(
                and_(
                    ShortBinding.is_active.is_(True),
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
    default_adapter: str = "n9",
) -> str:
    """
    Decide which shortener adapter to use for given scope.
    Returns adapter name (e.g., 'n9', 'bitly', ...). Falls back to default_adapter.
    """
    row = _pick(db, service=service, country=country, target=target)
    return row.adapter if row else default_adapter


def get_binding(
    db: Session,
    *,
    service: Optional[str] = None,
    country: Optional[str] = None,
    target: Optional[str] = None,
) -> Optional[ShortBinding]:
    """
    Public read helper: return the matching active ShortBinding for the given
    scope according to PRIORITY_MATRIX (or None if not found).
    """
    return _pick(db, service=service, country=country, target=target)


def set_binding(
    db: Session,
    *,
    adapter: str,
    service: Optional[str] = None,
    country: Optional[str] = None,
    target: Optional[str] = None,
    is_active: bool = True,
) -> ShortBinding:
    """
    Backward-compatible alias for set_short_binding so older imports
    (`from bot.services.short_policy import set_binding`) keep working.
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
    adapter: str,
    service: Optional[str] = None,
    country: Optional[str] = None,
    target: Optional[str] = None,
    is_active: bool = True,
) -> ShortBinding:
    """
    Upsert-like helper: deactivate previous bindings for the *exact* same scope
    and insert a new active binding.
    """
    # Deactivate previous exact-scope bindings
    to_disable = (
        select(ShortBinding)
        .where(
            and_(
                ShortBinding.service.is_(service) if service is None else ShortBinding.service == service,
                ShortBinding.country.is_(country) if country is None else ShortBinding.country == country,
                ShortBinding.target.is_(target) if target is None else ShortBinding.target == target,
                ShortBinding.is_active.is_(True),
            )
        )
    )
    for row in db.exec(to_disable).all():
        row.is_active = False
        db.add(row)

    # Insert new binding
    new_row = ShortBinding(
        adapter=adapter,
        service=service,
        country=country,
        target=target,
        is_active=is_active,
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
      - "all": deactivate everything
      - "unused": deactivate rows whose adapter is no longer referenced (kept simple)
      - "service:*": deactivate all service-level bindings
      - "country:*": deactivate all country-level bindings
      - "target:*": deactivate all target-level bindings
    Returns number of rows affected.
    """
    filters = [ShortBinding.is_active.is_(True)]

    if scope == "all":
        pass
    elif scope == "unused":
        # Basic heuristic: deactivate duplicates leaving the latest per (service,country,target)
        sub = (
            select(ShortBinding.service, ShortBinding.country, ShortBinding.target)
            .group_by(ShortBinding.service, ShortBinding.country, ShortBinding.target)
        )
        # Deactivate everything not the newest per group
        # For simplicity, mark all active as inactive except the newest per group found below
        newest_ids = set()
        for svc, ctry, tgt in db.exec(sub).all():
            newest = (
                select(ShortBinding.id)
                .where(
                    and_(
                        ShortBinding.service.is_(svc) if svc is None else ShortBinding.service == svc,
                        ShortBinding.country.is_(ctry) if ctry is None else ShortBinding.country == ctry,
                        ShortBinding.target.is_(tgt) if tgt is None else ShortBinding.target == tgt,
                        ShortBinding.is_active.is_(True),
                    )
                )
                .order_by(ShortBinding.id.desc())
                .limit(1)
            )
            row_id = db.exec(newest).scalar()
            if row_id:
                newest_ids.add(row_id)
        q = select(ShortBinding).where(ShortBinding.is_active.is_(True))
        rows = db.exec(q).all()
        affected = 0
        for r in rows:
            if r.id not in newest_ids:
                r.is_active = False
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
        # Unknown scope → do nothing
        return 0

    q = select(ShortBinding).where(and_(*filters))
    rows = db.exec(q).all()
    for r in rows:
        r.is_active = False
        db.add(r)
    db.commit()
    return len(rows)