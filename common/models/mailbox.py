from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint


class Mailbox(SQLModel, table=True):
    """Тип почты (почта/рубрика почт): gmail, main и кастомные.

    code — короткий код для имён файлов шаблонов (olx.pl-<code>.html).
    name — отображаемое имя / значение kind у SMTP и mailbox у шаблонов.
    """
    __tablename__ = "mailbox"
    __table_args__ = (
        UniqueConstraint("code", name="uq_mailbox_code"),
        UniqueConstraint("name", name="uq_mailbox_name"),
    )

    id: int | None = Field(default=None, primary_key=True)
    code: str = Field(index=True)
    name: str = Field(index=True)
    is_builtin: bool = False
    # Домены получателей, относящиеся к этому типу почты (через пробел/запятую/перенос).
    # По домену получателя автоматически выбирается тип почты → SMTP-группа.
    domains: str | None = None
