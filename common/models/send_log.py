from sqlmodel import SQLModel, Field

class SendLog(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    user_id: int | None = None
    service_id: int | None = None
    smtp_id: int | None = None
    email: str
    short_link_id: int | None = None
    status: str  # ok|fail
    error: str | None = None
    created_at: str  # ISO for skeleton
