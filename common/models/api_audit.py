"""Аудит вызовов публичного API (п.47).

Каждый HTTP-вызов /api/* пишется мидлварой (api/app.py): время, токен (id+префикс,
если распознан), IP, метод, путь, статус ответа. Нужен для разбора инцидентов и
подозрительной активности. Создаётся через create_all (импорт в bot/main.py).
"""
from sqlmodel import SQLModel, Field


class ApiAudit(SQLModel, table=True):
    __tablename__ = "api_audit"

    id: int | None = Field(default=None, primary_key=True)
    created_at: str = Field(index=True)
    token_id: int | None = Field(default=None, index=True)  # None — токен не распознан
    token_prefix: str | None = None                          # замаскированный префикс из заголовка
    ip: str | None = None
    method: str | None = None
    path: str | None = None
    status: int | None = None                                # HTTP-код ответа
