from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint


class ScopeCfg(SQLModel, table=True):
    """Настройки SMTP/SHORT на область (страна × сервис × рубрика), '*'/0 = ALL.

    При отправке из всех подходящих областей побеждает настроенная ПОСЛЕДНЕЙ
    (по `smtp_at` для SMTP-базы и `short_at` для сокращателя).
    """
    __tablename__ = "scope_cfg"
    __table_args__ = (UniqueConstraint("country", "service", "rubric_id", name="uq_scope_cfg"),)

    id: int | None = Field(default=None, primary_key=True)
    country: str = "*"       # код страны или '*'
    service: str = "*"       # сервис или '*'
    rubric_id: int = 0       # id рубрики или 0 (все)

    short: str | None = None      # провайдер ('n9.cl') | 'off' | None (не задано)
    short_at: str | None = None   # ISO, когда настроен SHORT
    smtp_at: str | None = None    # ISO, когда последний раз менялась SMTP-база
    autoswitch: bool = True       # автосмена при исчерпании лимита
    switch_limit: int = 0         # лимит автосмены: отправок с одного SMTP до смены (0 = лимит аккаунта)
    wrap: bool = False            # по кругу: база исчерпана по лимитам → сброс счётчиков и снова с первого
    current_smtp_id: int | None = None  # устарело (до п.30), не используется
    current_key: str | None = None      # текущий аккаунт базы: 'smtp:{id}' | 'aws:{id}' (автосмена / принуд. смена)


class ScopeSmtp(SQLModel, table=True):
    """SMTP-аккаунт в базе области. sent_count — счётчик автосмены ПО ЭТОЙ области
    (один аккаунт в разных областях считается раздельно)."""
    __tablename__ = "scope_smtp"
    __table_args__ = (UniqueConstraint("scope_id", "smtp_id", name="uq_scope_smtp"),)

    id: int | None = Field(default=None, primary_key=True)
    scope_id: int = Field(foreign_key="scope_cfg.id", index=True)
    smtp_id: int = Field(index=True)  # без FK: глобальная чистка SMTP не должна падать
    sent_count: int = 0              # отправлено с этого SMTP в этой области


class ScopeAws(SQLModel, table=True):
    """AWS SES-аккаунт в базе области (грузится вместе с SMTP: region:accesskey:secretkey:emailfrom).
    sent_count — счётчик автосмены ПО ЭТОЙ области."""
    __tablename__ = "scope_aws"
    __table_args__ = (UniqueConstraint("scope_id", "aws_id", name="uq_scope_aws"),)

    id: int | None = Field(default=None, primary_key=True)
    scope_id: int = Field(foreign_key="scope_cfg.id", index=True)
    aws_id: int = Field(index=True)
    sent_count: int = 0              # отправлено с этого AWS в этой области
