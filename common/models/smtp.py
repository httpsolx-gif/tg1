from sqlmodel import SQLModel, Field

class SmtpAccount(SQLModel, table=True):
    __tablename__ = "smtp"
    id: int | None = Field(default=None, primary_key=True)
    kind: str  # gmail|main
    country: str | None = None
    host: str
    port: int
    login: str
    email: str
    password_enc: str
    pool: str = "manual"  # manual | auto
    is_active: bool = True
    error_count: int = 0
    send_limit: int = 0   # 0 = без лимита
    sent_count: int = 0   # сколько успешных отправок сделано
