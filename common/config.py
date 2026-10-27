from pydantic_settings import BaseSettings
from typing import Optional, Set

class Settings(BaseSettings):
    BOT_TOKEN: str
    ADMIN_IDS: str = "1424975206"
    TG_PROXY: Optional[str] = None

    ERR_CHANNEL_ID: str = ""
    SEND_CHANNEL_ID: str = ""

    DATABASE_URL: str = "sqlite:///./dev.db"

    LOG_LEVEL: str = "INFO"
    LOG_TO_FILES: int = 1
    LOG_RETENTION_DAYS: int = 14

    API_LOG_AUTH_TOKEN: str = "supersecret"

    # Кастом-эмодзи iOS-иконки (работают только на eligible-боте: владелец Premium
    # или Fragment-юзернейм). На неподходящем боте оставить 0 — будут юникод-эмодзи.
    USE_CUSTOM_EMOJI: int = 1

    class Config:
        env_file = ".env"
        extra = "ignore"

    @property
    def admin_ids_set(self) -> Set[int]:
        return {int(x) for x in self.ADMIN_IDS.replace(",", " ").split() if x.isdigit()}

settings = Settings()
