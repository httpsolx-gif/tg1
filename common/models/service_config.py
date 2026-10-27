from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint


class ServiceRubricConfig(SQLModel, table=True):
    """Переопределение имени отправителя/тайтла для пары (сервис, рубрика).

    При отправке на сервис olx.pl в рубрике slayer — берём sender/title отсюда,
    иначе дефолт рубрики.
    """
    __tablename__ = "service_rubric_config"
    __table_args__ = (UniqueConstraint("service", "rubric_id", "mailbox", name="uq_service_rubric_mailbox"),)

    id: int | None = Field(default=None, primary_key=True)
    service: str = Field(index=True)
    rubric_id: int = Field(foreign_key="rubric.id", index=True)
    mailbox: str = Field(default="", index=True)  # тип почты (main/gmail/...); '' = любой
    sender_name: str | None = None
    title: str | None = None
    template_html: str | None = None  # переопределение шаблона (иначе — по рубрике)
