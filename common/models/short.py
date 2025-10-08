from __future__ import annotations

from datetime import datetime
from typing import Optional
from enum import Enum

from sqlalchemy import UniqueConstraint
from sqlmodel import SQLModel, Field


class ShortScope(str, Enum):
    global_ = "global"
    country = "country"
    service = "service"

class ShortTarget(str, Enum):
    gmail = "gmail"
    main = "main"


class ShortLink(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    original_url: str
    token: str
    service_id: int | None = None
    user_id: int | None = None


class ShortBinding(SQLModel, table=True):
    """
    Политика включения шортенера (N9) по умолчанию.

    Приоритет разрешения:
      service > country > target > global
    """
    __tablename__ = "shortbinding"
    __table_args__ = (
        UniqueConstraint("scope", "country", "service", "target", name="uq_shortbinding"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)

    # "global" | "country" | "service"
    scope: ShortScope = Field(index=True)

    # только для scope="country"
    country: Optional[str] = Field(default=None, index=True)

    # только для scope="service"
    service: Optional[str] = Field(default=None, index=True)

    # "gmail" | "main"
    target: Optional[ShortTarget] = Field(default=None, index=True)

    # включено/выключено
    enabled: bool = Field(default=True)

    created_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)
    updated_at: datetime = Field(default_factory=datetime.utcnow, nullable=False)