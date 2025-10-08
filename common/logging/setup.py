import logging, os
from logging.handlers import RotatingFileHandler
from .json_formatter import JsonFormatter
from .tg_handlers import TelegramChannelHandler
# try to import OnlySends; provide a safe fallback if it's missing
try:
    from .filters import OnlySends  # type: ignore
except Exception:
    class OnlySends(logging.Filter):
        """Fallback filter: allow logs explicitly marked as send events."""
        def filter(self, record: logging.LogRecord) -> bool:
            if getattr(record, "is_send", False):
                return True
            name = getattr(record, "name", "")
            if isinstance(name, str) and name.endswith(".sends"):
                return True
            extra = getattr(record, "__dict__", {})
            return bool(extra.get("is_send"))
from common.config import settings

LOG_DIR = os.path.join(os.getcwd(), "logs")

def configure_logging(bot_token: str):
    os.makedirs(LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))

    console = logging.StreamHandler()
    console.setFormatter(JsonFormatter())
    root.addHandler(console)

    # Add separate handler for errors to stderr in human-readable format
    errors_console = logging.StreamHandler()
    errors_console.setLevel(logging.ERROR)
    errors_console.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(name)s - %(message)s"))
    root.addHandler(errors_console)

    # Files
    if settings.LOG_TO_FILES:
        err_file = RotatingFileHandler(os.path.join(LOG_DIR, "errors.jsonl"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        err_file.setLevel(logging.WARNING)
        err_file.setFormatter(JsonFormatter())
        root.addHandler(err_file)

        sends_file = RotatingFileHandler(os.path.join(LOG_DIR, "sends.jsonl"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        sends_file.setLevel(logging.INFO)
        sends_file.setFormatter(JsonFormatter())
        sends_file.addFilter(OnlySends())
        root.addHandler(sends_file)

        debug_file = RotatingFileHandler(os.path.join(LOG_DIR, "debug.jsonl"), maxBytes=5_000_000, backupCount=5, encoding="utf-8")
        debug_file.setLevel(logging.DEBUG)
        debug_file.setFormatter(JsonFormatter())
        root.addHandler(debug_file)

    # Telegram channels
    if settings.SEND_CHANNEL_ID:
        send_handler = TelegramChannelHandler(bot_token, settings.SEND_CHANNEL_ID, level=logging.INFO)
        send_handler.setFormatter(logging.Formatter("📤 %(msg)s"))
        send_handler.addFilter(OnlySends())
        root.addHandler(send_handler)

    if settings.ERR_CHANNEL_ID:
        err_handler = TelegramChannelHandler(bot_token, settings.ERR_CHANNEL_ID, level=logging.WARNING)
        err_handler.setFormatter(logging.Formatter("🚨 %(levelname)s: %(msg)s"))
        root.addHandler(err_handler)

    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiogram").setLevel(logging.INFO)
