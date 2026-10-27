from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint


class EmailTemplate(SQLModel, table=True):
    """HTML-шаблон письма, определяемый по имени файла `<service>-<код>.html`.

    service — сайт/сервис (olx.pl), mailbox — тип почты (main|gmail|hotmail|...).
    Пара (service, mailbox) уникальна — повторная загрузка обновляет шаблон.
    """
    __tablename__ = "email_template"
    __table_args__ = (UniqueConstraint("service", "mailbox", name="uq_template_service_mailbox"),)

    id: int | None = Field(default=None, primary_key=True)
    service: str = Field(index=True)
    mailbox: str = Field(index=True)
    html: str
    filename: str | None = None
    created_at: str | None = None
