"""Авто-проверка на спам (п.55).

`SpamSeed` — сид-ящик: куда шлём пробу (через наш сервис) и как потом зайти по IMAP,
чтобы посмотреть, в какой папке письмо (Входящие / Спам). Пароль — app-password,
хранится plaintext (как остальные креды в проекте; encrypt_password — заглушка).

`SpamCheckResult` — результат одной проверки (размещение + SPF/DKIM/DMARC).
"""
from sqlmodel import SQLModel, Field


class SpamSeed(SQLModel, table=True):
    __tablename__ = "spam_seed"
    id: int | None = Field(default=None, primary_key=True)
    email: str                         # адрес, на который шлём пробу
    imap_host: str                     # IMAP-сервер (автоопределяется по домену)
    imap_port: int = 993
    imap_ssl: bool = True
    imap_user: str | None = None       # None → совпадает с email
    imap_pass: str = ""                # app-password (plaintext, как в проекте)
    country: str                       # страна из каталога — чем шлём пробу
    service: str                       # сервис из каталога этой страны
    label: str | None = None           # подпись провайдера (Gmail/Outlook/…)
    active: bool = True
    created_at: str = ""


class SpamCheckResult(SQLModel, table=True):
    __tablename__ = "spam_result"
    id: int | None = Field(default=None, primary_key=True)
    seed_id: int = Field(index=True)
    ts: str
    placement: str                     # inbox|spam|missing|send_fail|imap_error
    spf: str | None = None
    dkim: str | None = None
    dmarc: str | None = None
    spam_score: str | None = None
    sender: str | None = None          # From, которым ушла проба
    via: str | None = None             # транспорт (SES/SMTP/нода)
    detail: str | None = None          # текст ошибки/примечание
