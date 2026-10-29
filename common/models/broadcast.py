"""Авторассылка по группам от имени пользователя (userbot, Telethon) — п.56.

Отдельный процесс `userbot/` логинится ПОД аккаунтом пользователя (MTProto) и постит
фото+текст по списку групп, где пользователь состоит и постинг разрешён. Управление —
из админки бота (разделы пишут сюда, userbot читает из общей БД).

`BcState` — единственная строка-конфиг (id=1): текст, фото, интервал, задержки, лимит/сутки,
счётчики, флаги enabled/run_now. `BcTarget` — целевая группа (ссылка/username/id).
"""
from sqlmodel import SQLModel, Field


class BcState(SQLModel, table=True):
    __tablename__ = "bc_state"
    id: int | None = Field(default=1, primary_key=True)
    enabled: bool = False              # включён ли фоновый постинг
    run_now: bool = False              # разовый запуск по кнопке «Отправить сейчас»
    caption: str = ""                  # текст сообщения (подпись к фото)
    media_path: str | None = None      # локальный путь к фото (одно на всю рассылку)
    interval_min: int = 60             # период между полными проходами, мин
    delay_min: int = 8                 # мин. пауза между группами, сек (антифлуд)
    delay_max: int = 25                # макс. пауза между группами, сек
    daily_cap: int = 200               # потолок сообщений в сутки (0 = без лимита)
    sent_today: int = 0                # счётчик за текущие сутки
    day: str = ""                      # YYYY-MM-DD — для сброса sent_today
    last_run: str = ""                 # ISO времени последнего прохода
    status: str = ""                   # краткий итог последнего прохода


class BcTarget(SQLModel, table=True):
    __tablename__ = "bc_target"
    id: int | None = Field(default=None, primary_key=True)
    chat_ref: str                      # @username / t.me/... / числовой id (-100…)
    title: str | None = None           # подпись/название (резолвится userbot'ом)
    active: bool = True
    created_at: str = ""
    last_ok: str | None = None         # ISO последней успешной отправки
    last_error: str | None = None      # текст последней ошибки
