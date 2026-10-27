from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint


class Rubric(SQLModel, table=True):
    """Рубрика — бакет настроек рассылки со своими smtp/шаблонами/сокрами/тайтлами/именами."""
    __tablename__ = "rubric"

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True)

    # Собственные настройки рубрики
    sender_name: str | None = None
    title: str | None = None
    smtp_group: str | None = None   # gmail|main
    shortener: str | None = None    # n9|...
    # Домены получателей этой рубрики (gmx.de web.de …). По домену письма
    # автоматически выбирается рубрика при отправке.
    domains: str | None = None
    created_at: str | None = None


class RubricMailbox(SQLModel, table=True):
    """Почта (mailbox-тип) внутри рубрики."""
    __tablename__ = "rubric_mailbox"
    __table_args__ = (UniqueConstraint("rubric_id", "mailbox", name="uq_rubric_mailbox"),)

    id: int | None = Field(default=None, primary_key=True)
    rubric_id: int = Field(foreign_key="rubric.id", index=True)
    mailbox: str   # main|gmail|hotmail|...
