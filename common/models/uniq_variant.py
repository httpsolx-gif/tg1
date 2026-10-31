from sqlmodel import SQLModel, Field


class UniqVariant(SQLModel, table=True):
    """Пул уникализированных вариантов HTML-шаблона (htmlmix getRandomizeLetter).

    API лимитирован 1 запросом в 60 с, поэтому за раз берём пачку вариантов
    (fileCountGen) и раздаём их по письмам из этого пула. `pool` группирует
    варианты одной области (service|rubric|mailbox), `sig` привязывает их к
    конкретному шаблону+настройкам: шаблон/опции изменились → sig другой →
    старые варианты игнорируются и вычищаются при следующем доливе.
    """
    __tablename__ = "uniq_variant"

    id: int | None = Field(default=None, primary_key=True)
    pool: str = Field(index=True)   # service|rubric_id|mailbox
    sig: str = Field(index=True)    # хэш(pool + opts + исходный шаблон)
    html: str                       # вариант шаблона (с плейсхолдерами *DATE* и т.п.)
    used: bool = Field(default=False, index=True)
    created: str = ""
