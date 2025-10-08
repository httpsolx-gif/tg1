from sqlmodel import SQLModel, Field, Relationship
from typing import Optional

class AutoSwitch(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    kind: str  # gmail|main
    scope: str  # global|country|service
    country: str | None = None
    service_id: int | None = None
    enabled: bool = False
    limit_per_account: int = 100

class SmtpBinding(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    group: str  # gmail|main
    scope: str  # global|country|service
    country: str | None = None
    service_id: int | None = None
    smtp_id: int = Field(foreign_key="smtp.id")
    active: bool = True

    smtp_account: Optional["SmtpAccount"] = Relationship()
