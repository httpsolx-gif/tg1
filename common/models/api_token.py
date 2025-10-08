from sqlmodel import SQLModel, Field

class ApiToken(SQLModel, table=True):
    id: str = Field(primary_key=True)  # e.g. t_xxx
    bot_link: str | None = None
    tsa_link: str | None = None
    ip_whitelist: str | None = None
    active: bool = True
