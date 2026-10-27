from sqlmodel import SQLModel, Field


class Node(SQLModel, table=True):
    """Нода отправки (сервер/прокси), через которую уходит SMTP.

    Формат добавления: `login ip pass`.
    port — на будущее (SOCKS/SSH), сейчас необязателен.
    """
    __tablename__ = "node"

    id: int | None = Field(default=None, primary_key=True)
    login: str
    ip: str
    password_enc: str
    port: int | None = None
    region: str | None = None  # гео AWS этой ноды (us-east-1 и т.п.); None = без региона
    is_active: bool = True
    error_count: int = 0
