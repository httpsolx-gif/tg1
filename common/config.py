from pydantic_settings import BaseSettings
from pydantic import field_validator
from typing import Optional, Set

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: str = "1424975206"
    TG_PROXY: Optional[str] = None

    ERR_CHANNEL_ID: str = ""
    SEND_CHANNEL_ID: str = ""

    # Бэкапы: канал для авто-дампов (бот должен быть админом) и период в часах (0 = выкл)
    BACKUP_CHANNEL_ID: str = ""
    BACKUP_EVERY_HOURS: int = 0

    # Авто-проверка на спам (п.55): канал для отчётов, период в минутах (0 = выкл),
    # пауза перед IMAP-проверкой и ссылка-кнопка в пробном письме.
    SPAMCHECK_CHANNEL_ID: str = ""
    SPAMCHECK_EVERY_MIN: int = 0
    SPAMCHECK_WAIT_SEC: int = 90
    SPAMCHECK_LINK: str = "https://www.google.com"

    # Авторассылка по группам от имени пользователя (userbot, Telethon — п.56).
    # API_ID/API_HASH — с my.telegram.org; сессия создаётся разово (python -m userbot.login).
    TG_API_ID: int = 0
    TG_API_HASH: str = ""
    USERBOT_SESSION: str = "userbot/account.session"
    USERBOT_PHONE: str = ""           # необязательно (можно ввести при логине)

    DATABASE_URL: str = "sqlite:///./dev.db"

    # Лидер-лок (п.57): основной + резервный экземпляр бота/userbot на разных серверах.
    # 0 = выкл (работать без резерва). RETRY — как часто резерв пробует взять лок,
    # HEARTBEAT — как часто лидер проверяет, что лок ещё его (иначе выходит).
    LEADER_LOCK: int = 1
    LEADER_RETRY_SEC: int = 5
    LEADER_HEARTBEAT_SEC: int = 5

    LOG_LEVEL: str = "INFO"
    LOG_TO_FILES: int = 1
    LOG_RETENTION_DAYS: int = 14

    API_LOG_AUTH_TOKEN: str = "supersecret"

    # Кастом-эмодзи iOS-иконки (работают только на eligible-боте: владелец Premium
    # или Fragment-юзернейм). На неподходящем боте оставить 0 — будут юникод-эмодзи.
    USE_CUSTOM_EMOJI: int = 1

    @field_validator("TG_API_ID", mode="before")
    @classmethod
    def _blank_int_zero(cls, v):
        # пустая строка в .env (TG_API_ID=) → 0, иначе pydantic падает на int-парсинге
        if v is None or (isinstance(v, str) and not v.strip()):
            return 0
        return v

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def admin_ids_set(self) -> Set[int]:
        return {int(x) for x in self.ADMIN_IDS.replace(",", " ").split() if x.isdigit()}

settings = Settings()
