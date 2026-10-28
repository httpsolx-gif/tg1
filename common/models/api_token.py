"""Токен публичного API отправки (хэш-схема, раздел 3 api/README.md).

В БД хранится ТОЛЬКО sha256(token) — не сам токен. Полное значение показываем
админу один раз при создании; дальше для идентификации — `prefix`.
Выдача/отзыв/удаление — только в Telegram (раздел 🛡 API). Через сам API
управлять токенами нельзя.

Таблица создаётся через create_all (регистрируется в bot/main.py), не в alembic.
"""
from sqlmodel import SQLModel, Field


class ApiToken(SQLModel, table=True):
    __tablename__ = "api_token"

    id: int | None = Field(default=None, primary_key=True)
    prefix: str = Field(index=True)          # публичный идентификатор для списка (mk_live_ab12cd…)
    token_hash: str = Field(index=True)      # sha256(полный токен) — поиск по хэшу
    label: str | None = None                 # кому выдан / метка

    scopes: str = "send"                     # csv прав: send[,logs:read,…]
    allowed_services: str | None = None      # csv разрешённых сервисов (пусто → ни одного)
    allowed_link_domains: str | None = None  # csv доменов, на которые можно слать ссылки
    ip_whitelist: str | None = None          # csv IP/CIDR (пусто → без ограничения по IP)

    daily_quota: int = 0                     # писем/сутки (0 = без лимита)
    rate_per_min: int = 0                     # запросов/минуту (0 = без лимита)
    sent_today: int = 0                       # учёт суточной квоты
    quota_reset_at: str | None = None         # ISO-дата, когда sent_today сброшен

    active: bool = True                       # мгновенный отзыв
    expires_at: str | None = None             # ISO, авто-истечение (пусто → бессрочно)

    created_at: str                           # ISO
    last_used_at: str | None = None           # ISO последнего вызова
    last_ip: str | None = None                # IP последнего вызова (аудит)
