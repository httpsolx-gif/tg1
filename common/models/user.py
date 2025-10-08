from typing import Optional
from sqlmodel import SQLModel, Field

class User(SQLModel, table=True):
    id: int = Field(primary_key=True)
    username: Optional[str] = None
    is_admin: bool = False
    is_blocked: bool = False
    subscription_until: Optional[str] = None  # ISO string for simplicity
