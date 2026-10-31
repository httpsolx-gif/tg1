from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint


class ServiceRubricConfig(SQLModel, table=True):
    """Переопределение имени отправителя/тайтла/шаблона для пары (сервис, рубрика).

    При отправке на сервис olx.pl в рубрике slayer — по каждому полю: конкретная рубрика →
    ALL рубрик сервиса (rubric_id IS NULL) → дефолт рубрики.
    """
    __tablename__ = "service_rubric_config"
    __table_args__ = (UniqueConstraint("service", "rubric_id", "mailbox", name="uq_service_rubric_mailbox"),)

    id: int | None = Field(default=None, primary_key=True)
    service: str = Field(index=True)
    # NULL = ALL (все рубрики сервиса, п.62): общие имя/тайтл/шаблон; конкретная рубрика перекрывает
    rubric_id: int | None = Field(default=None, foreign_key="rubric.id", index=True)
    mailbox: str = Field(default="", index=True)  # тип почты (main/gmail/...); '' = любой
    sender_name: str | None = None
    title: str | None = None
    template_html: str | None = None  # переопределение шаблона (иначе — по рубрике)
    template_name: str | None = None  # имя файла, из которого поставлен шаблон (п.65, для карточки)
    uniq_opts: str | None = None  # JSON настроек уникализации htmlmix (п.106); None = выкл/дефолт
