"""Простое key-value состояние приложения (create_all, не alembic).

Используется для курсора выгрузки базы email (sends_cursor) и подобных мелочей.
"""
from sqlmodel import SQLModel, Field


class AppState(SQLModel, table=True):
    __tablename__ = "app_state"

    key: str = Field(primary_key=True)
    value: str | None = None
