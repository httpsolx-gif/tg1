from sqlmodel import SQLModel, Field

class Click(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    short_link_id: int
    mailbox_domain: str
    ip: str | None = None
    ua: str | None = None
    ts: str
