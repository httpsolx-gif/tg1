from sqlmodel import SQLModel, Field


class Country(SQLModel, table=True):
    """Страна каталога (п.61). Сервисы страны — таблица `service` (Service.country = code)."""
    __tablename__ = "country"

    code: str = Field(primary_key=True)  # ISO-код в верхнем регистре: PL, DE, …
    flag: str = ""                       # эмодзи-флаг (по умолчанию из кода)
    sort: int = 0                        # порядок вывода (новые — в конец)
