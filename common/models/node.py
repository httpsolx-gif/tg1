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
    purpose: str = "send"      # роль: 'send' (отправка) | 'short' (сокращалка) | 'both'
    is_backup: bool = False    # резервная: берётся для отправки, только если основные не отвечают (п.59)
    is_active: bool = True
    error_count: int = 0

    def has_role(self, role: str) -> bool:
        p = (self.purpose or "send")
        return p == "both" or p == role
