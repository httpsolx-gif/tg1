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
    # встроенная картинка шаблона (п.128): base64 + MIME-тип; плейсхолдер _src1_ → cid:cid_src1
    image_b64: str | None = None
    image_ct: str | None = None
