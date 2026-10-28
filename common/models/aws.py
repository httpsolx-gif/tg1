from sqlmodel import SQLModel, Field


class AwsAccount(SQLModel, table=True):
    """Аккаунт AWS SES (отправка через API).

    Формат добавления: region:accesskey:secretkey:emailfrom
    """
    __tablename__ = "aws_account"

    id: int | None = Field(default=None, primary_key=True)
    region: str
    access_key: str
    secret_key_enc: str
    email_from: str
    # опциональная привязка к ноде: SES-запрос выполняется на этой ноде (её IP)
    node_id: int | None = Field(default=None, foreign_key="node.id")
    is_active: bool = True
    error_count: int = 0
    send_limit: int = 0   # 0 = без лимита (как у SMTP)
    sent_count: int = 0   # успешные отправки — для лимита/автосмены базы области
