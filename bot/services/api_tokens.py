"""Сервис токенов публичного API (раздел 3–4 api/README.md).

Хранение — только sha256(token). Полный токен возвращается ОДИН раз при создании
(вызывающий показывает его админу и больше нигде не сохраняет). Поиск/сверка —
по хэшу через hmac.compare_digest (constant-time).

Чистый модуль: только sqlmodel + common (без aiogram), поэтому его одинаково
импортируют и бот (админка 🛡 API), и API-процесс (auth).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from sqlmodel import Session, select

from common.db import get_engine
from common.models.api_token import ApiToken

_engine = get_engine()

PREFIX = "mk_live_"
SCOPES_ALL = ("send", "logs:read")


# ----------------------------- утилиты -----------------------------
def _now() -> datetime:
    return datetime.utcnow()


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _hash(raw: str) -> str:
    return hashlib.sha256((raw or "").encode("utf-8")).hexdigest()


def _generate() -> tuple[str, str]:
    """→ (полный токен, публичный префикс). Префикс = PREFIX + первые 6 символов тела."""
    body = secrets.token_urlsafe(32)
    raw = PREFIX + body
    prefix = PREFIX + body[:6]
    return raw, prefix


def _csv(value: str | None) -> list[str]:
    return [x.strip() for x in (value or "").replace(",", " ").split() if x.strip()]


# ----------------------------- CRUD (админка) -----------------------------
def create(
    label: str | None = None,
    *,
    scopes: str = "send",
    allowed_services: str | None = "*",   # по умолчанию ВСЕ сервисы
    allowed_link_domains: str | None = "*",  # по умолчанию любые домены ссылок
    daily_quota: int = 0,                 # 0 = без лимита (всегда)
    rate_per_min: int = 0,                # 0 = без лимита
    expires_days: int = 0,                # 0 = бессрочно (всегда)
    ip_whitelist: str | None = None,
) -> tuple[ApiToken, str]:
    """Создать токен. Возвращает (запись, ПОЛНЫЙ токен) — токен показать один раз."""
    raw, prefix = _generate()
    expires_at = _iso(_now() + timedelta(days=expires_days)) if expires_days > 0 else None
    tok = ApiToken(
        prefix=prefix,
        token_hash=_hash(raw),
        label=(label or "").strip() or None,
        scopes=scopes or "send",
        allowed_services=allowed_services,
        allowed_link_domains=allowed_link_domains,
        ip_whitelist=ip_whitelist,
        daily_quota=max(0, int(daily_quota or 0)),
        rate_per_min=max(0, int(rate_per_min or 0)),
        active=True,
        expires_at=expires_at,
        created_at=_iso(_now()),
    )
    with Session(_engine) as db:
        db.add(tok)
        db.commit()
        db.refresh(tok)
    return tok, raw


def list_tokens() -> list[ApiToken]:
    with Session(_engine) as db:
        return db.exec(select(ApiToken).order_by(ApiToken.id.desc())).all()


def get(tid: int) -> ApiToken | None:
    with Session(_engine) as db:
        return db.get(ApiToken, tid)


def revoke(tid: int) -> bool:
    """Отозвать (active=False) — мгновенно; запись и история сохраняются."""
    with Session(_engine) as db:
        t = db.get(ApiToken, tid)
        if not t:
            return False
        t.active = False
        db.add(t)
        db.commit()
        return True


def enable(tid: int) -> bool:
    with Session(_engine) as db:
        t = db.get(ApiToken, tid)
        if not t:
            return False
        t.active = True
        db.add(t)
        db.commit()
        return True


def delete(tid: int) -> bool:
    """Полностью убрать запись токена (чистка списка). История SendLog остаётся."""
    with Session(_engine) as db:
        t = db.get(ApiToken, tid)
        if not t:
            return False
        db.delete(t)
        db.commit()
        return True


# поля, редактируемые из карточки (имя → (атрибут, тип))
_STR_FIELDS = {"label", "allowed_services", "allowed_link_domains", "ip_whitelist", "scopes"}
_INT_FIELDS = {"daily_quota", "rate_per_min"}


def set_field(tid: int, field: str, value: str) -> bool:
    """Обновить одно поле токена из админки. Пустая строка/«-»/«нет» → очистить."""
    with Session(_engine) as db:
        t = db.get(ApiToken, tid)
        if not t:
            return False
        v = (value or "").strip()
        if field in _STR_FIELDS:
            cleared = v.lower() in ("", "-", "нет", "none", "*пусто*")
            if field == "scopes":
                # валидируем только из известного набора; по умолчанию send
                allowed = [s for s in _csv(v) if s in SCOPES_ALL] or ["send"]
                setattr(t, field, ",".join(allowed))
            else:
                setattr(t, field, None if cleared else ",".join(_csv(v)) or None)
        elif field in _INT_FIELDS:
            try:
                setattr(t, field, max(0, int(v)))
            except ValueError:
                return False
        elif field == "expires_days":
            try:
                days = int(v)
            except ValueError:
                return False
            t.expires_at = _iso(_now() + timedelta(days=days)) if days > 0 else None
        else:
            return False
        db.add(t)
        db.commit()
        return True


# ----------------------------- проверки (API-сторона) -----------------------------
def _expired(t: ApiToken) -> bool:
    if not t.expires_at:
        return False
    try:
        return _now() >= datetime.fromisoformat(t.expires_at)
    except ValueError:
        return False


def lookup(raw: str) -> ApiToken | None:
    """Найти активный непросроченный токен по полному значению (constant-time сверка).

    Возвращает запись ТОЛЬКО если токен активен и не истёк. IP/scope/квоту
    проверяет вызывающий (api/auth.py) — у него есть контекст запроса.
    """
    if not raw:
        return None
    h = _hash(raw)
    with Session(_engine) as db:
        for t in db.exec(select(ApiToken).where(ApiToken.active == True)).all():  # noqa: E712
            if hmac.compare_digest(t.token_hash, h):
                return None if _expired(t) else t
    return None


def has_scope(t: ApiToken, scope: str) -> bool:
    return scope in _csv(t.scopes)


def allows_service(t: ApiToken, service: str) -> bool:
    svcs = _csv(t.allowed_services)
    return bool(svcs) and (service in svcs or "*" in svcs)


def allows_link_domain(t: ApiToken, domain: str) -> bool:
    doms = _csv(t.allowed_link_domains)
    return bool(doms) and (domain in doms or "*" in doms)


def ip_allowed(t: ApiToken, ip: str | None) -> bool:
    """Пустой whitelist → разрешено всем. Иначе точное совпадение IP или CIDR."""
    wl = _csv(t.ip_whitelist)
    if not wl:
        return True
    if not ip:
        return False
    import ipaddress
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    for item in wl:
        try:
            if "/" in item:
                if addr in ipaddress.ip_network(item, strict=False):
                    return True
            elif addr == ipaddress.ip_address(item):
                return True
        except ValueError:
            continue
    return False


def touch(tid: int, ip: str | None = None) -> None:
    """Обновить last_used_at/last_ip после успешного вызова (аудит)."""
    try:
        with Session(_engine) as db:
            t = db.get(ApiToken, tid)
            if not t:
                return
            t.last_used_at = _iso(_now())
            if ip:
                t.last_ip = ip
            db.add(t)
            db.commit()
    except Exception:
        pass


def consume_quota(tid: int, n: int = 1) -> bool:
    """Списать n из суточной квоты (сброс по дате). → True если в пределах лимита.

    daily_quota == 0 → без лимита (всегда True, счётчик всё равно ведём для статистики).
    """
    with Session(_engine) as db:
        t = db.get(ApiToken, tid)
        if not t:
            return False
        today = _now().date().isoformat()
        if t.quota_reset_at != today:
            t.sent_today = 0
            t.quota_reset_at = today
        if t.daily_quota and t.sent_today + n > t.daily_quota:
            db.add(t)
            db.commit()
            return False
        t.sent_today = max(0, t.sent_today + n)  # n<0 — возврат; после смены суток не уходим в минус
        db.add(t)
        db.commit()
        return True
