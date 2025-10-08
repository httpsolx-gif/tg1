from __future__ import annotations

from typing import Optional, Literal, Tuple, List
from sqlmodel import select, Session

from common.db import engine as _engine
from common.models.smtp import SmtpAccount

# Попробуем подтянуть модели для привязок/автосмены, если они уже есть в проекте
try:  # ожидаем, что тут будет храниться фиксация выбранного SMTP
    from common.models.autoswitch import SmtpBinding  # type: ignore
except Exception:  # модель ещё не создана — работаем без неё
    SmtpBinding = None  # type: ignore

# Типы областей и групп
Scope = Literal["global", "country", "service"]
Group = Literal["gmail", "main"]


# =============================
# Публичный интерфейс модуля
# =============================
async def set_binding(
    *,
    group: Group,
    scope: Scope,
    smtp_id: int,
    country_code: Optional[str] = None,
    service_id: Optional[int] = None,
) -> None:
    """Зафиксировать конкретный SMTP как текущий для области.

    Если модель SmtpBinding отсутствует — бросаем понятную ошибку,
    чтобы добавить её миграцией. Админ-меню может поймать это и
    показать предупреждение.
    """
    if SmtpBinding is None:
        raise RuntimeError(
            "SmtpBinding model is missing. Add common.models.autoswitch.SmtpBinding and run migrations."
        )

    _validate_scope_args(scope, country_code, service_id)

    with Session(_engine) as db:
        # проверим, что smtp существует и активен
        smtp = db.get(SmtpAccount, smtp_id)
        if not smtp or not smtp.is_active:
            raise ValueError("SMTP not found or inactive")

        # один биндинг на область (upsert)
        stmt = select(SmtpBinding).where(
            (SmtpBinding.group == group) & (SmtpBinding.scope == scope)
        )
        if scope == "country":
            stmt = stmt.where(SmtpBinding.country_code == country_code)
        elif scope == "service":
            stmt = stmt.where(SmtpBinding.service_id == service_id)

        row = db.exec(stmt).first()
        if row:
            row.smtp_id = smtp_id
        else:
            row = SmtpBinding(
                group=group,
                scope=scope,
                smtp_id=smtp_id,
                country_code=country_code,
                service_id=service_id,
            )
            db.add(row)
        db.commit()


async def get_binding(
    *,
    group: Group,
    scope: Scope,
    country_code: Optional[str] = None,
    service_id: Optional[int] = None,
) -> Optional[int]:
    """Получить зафиксированный smtp_id для области, если он есть."""
    if SmtpBinding is None:
        return None

    _validate_scope_args(scope, country_code, service_id)

    with Session(_engine) as db:
        stmt = select(SmtpBinding.smtp_id).where(
            (SmtpBinding.group == group) & (SmtpBinding.scope == scope)
        )
        if scope == "country":
            stmt = stmt.where(SmtpBinding.country_code == country_code)
        elif scope == "service":
            stmt = stmt.where(SmtpBinding.service_id == service_id)
        val = db.exec(stmt).first()
        return int(val) if val is not None else None


async def pick_smtp(
    *,
    group: Group,
    country_code: Optional[str] = None,
    service_id: Optional[int] = None,
) -> Optional[SmtpAccount]:
    """
    Вернуть SMTP для отправки по приоритету привязок:
      1) service → 2) country → 3) global → 4) первый активный из группы (fallback)

    Здесь **не** реализуем лимиты/автосмену — это следующий шаг, когда пришлёшь autoswitch-модель.
    """
    # 1. service
    if service_id is not None:
        sid = await get_binding(group=group, scope="service", service_id=service_id)
        smtp = _load_active(sid)
        if smtp is not None:
            return smtp

    # 2. country
    if country_code:
        sid = await get_binding(group=group, scope="country", country_code=country_code)
        smtp = _load_active(sid)
        if smtp is not None:
            return smtp

    # 3. global
    sid = await get_binding(group=group, scope="global")
    smtp = _load_active(sid)
    if smtp is not None:
        return smtp

    # 4. fallback — первый активный из данной группы (чтобы не падать без причины)
    with Session(_engine) as db:
        row = db.exec(
            select(SmtpAccount)
            .where((SmtpAccount.kind == group) & (SmtpAccount.is_active == True))  # noqa: E712
            .order_by(SmtpAccount.last_used_at, SmtpAccount.id)
        ).first()
        return row


# =============================
# Вспомогательные функции
# =============================

def _validate_scope_args(scope: Scope, country_code: Optional[str], service_id: Optional[int]) -> None:
    if scope == "global":
        return
    if scope == "country" and not country_code:
        raise ValueError("country_code is required for country scope")
    if scope == "service" and service_id is None:
        raise ValueError("service_id is required for service scope")


def _load_active(smtp_id: Optional[int]) -> Optional[SmtpAccount]:
    if not smtp_id:
        return None
    with Session(_engine) as db:
        smtp = db.get(SmtpAccount, int(smtp_id))
        if smtp and smtp.is_active:
            return smtp
        return None
