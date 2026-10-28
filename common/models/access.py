"""Списки доступа отправителей (пользователей бота).

- `AccessEntry` — запись в одном из списков: ban / white / black. Значение — либо
  числовой Telegram id, либо @username (нормализованный, без @, lower).

Правило классификации (bot/services/access.classify): админ → white;
в ban → заблокирован; в white → обычная отправка; иначе (вкл. black и всех
не добавленных) → black.

Профиль отправки black (шаблон/тайтл/имя/SMTP/сокра) хранится в рубрике «black»
(common.models.rubric + service_rubric_config + scope), а не здесь.
"""
from sqlmodel import SQLModel, Field
from sqlalchemy import UniqueConstraint


class AccessEntry(SQLModel, table=True):
    __tablename__ = "access_entry"
    __table_args__ = (UniqueConstraint("lst", "value", name="uq_access_lst_value"),)

    id: int | None = Field(default=None, primary_key=True)
    lst: str                        # 'ban' | 'white' | 'black'
    value: str = Field(index=True)  # '123456789' (id) | 'username' (lower, без @)
    is_id: bool = False             # True = числовой id, False = username
    note: str | None = None


class SeenUser(SQLModel, table=True):
    """Виденные отправители: id → @username (для поиска/карточки в USERS).

    Пишется при каждой отправке (bot/services/access.remember_user). username —
    для НОВЫХ отправок; по старым (до появления таблицы) остаётся None.
    """
    __tablename__ = "seen_user"

    user_id: int = Field(primary_key=True)   # Telegram id
    username: str | None = Field(default=None, index=True)  # без @, lower
    first_seen: str | None = None
    last_seen: str | None = None
