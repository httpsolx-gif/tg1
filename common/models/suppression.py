"""Suppression-лист получателей (п.47).

Адреса, на которые НЕ слать (отписки/жалобы/хард-баунсы) — бережёт репутацию
доменов. Проверяется в send_core.build_payload (бот и API). Пополняется вручную в
Telegram (🛡 API → Suppression) или программно (при желании — из SES-нотификаций).
Создаётся через create_all (импорт в bot/main.py).
"""
from sqlmodel import SQLModel, Field


class Suppression(SQLModel, table=True):
    __tablename__ = "suppression"

    email: str = Field(primary_key=True)  # всегда в нижнем регистре
    reason: str | None = None             # bounce|complaint|manual|...
    created_at: str
