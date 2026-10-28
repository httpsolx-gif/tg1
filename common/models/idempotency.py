"""Идемпотентность приёма отправки (п.47).

Клиент может прислать заголовок `Idempotency-Key`; повтор с тем же ключом (в рамках
одного токена) не создаёт новую отправку, а возвращает прежний ответ. Ключ скоупится
на токен: PK = "{token_id}:{key}". Создаётся через create_all (импорт в bot/main.py).
"""
from sqlmodel import SQLModel, Field


class IdemKey(SQLModel, table=True):
    __tablename__ = "idem_key"

    id: str = Field(primary_key=True)  # "{token_id}:{idempotency_key}"
    response: str                       # JSON ответа, который вернули в первый раз
    created_at: str
