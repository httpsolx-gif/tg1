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
    # разрезы статистики (п.37)
    country: str | None = None        # код страны отправки
    service: str | None = None        # имя сервиса
    mailbox: str | None = None        # тип почты (gmail/main/...)
    short_provider: str | None = None # 'off'|'n9.cl'|'dom:{id}'
    track: str | None = Field(default=None, index=True)  # id для привязки перехода (личные домены)
