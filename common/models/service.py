from sqlmodel import SQLModel, Field

class Service(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    name: str
    slug: str
    country: str
    active: bool = True
