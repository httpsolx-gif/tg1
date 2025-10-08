# common/logging/filters.py  (добавь в существующий файл)
import logging
import re

class DropAiogramNoiseFilter(logging.Filter):
    """Режет шум типа 'Update id=... is handled' и прочие инфо-сообщения aiogram."""
    _noise = (
        re.compile(r"^Update id=\d+ is handled"),
        re.compile(r"^Start polling"),
        re.compile(r"^Run polling"),
        re.compile(r"^Polling stopped"),
        re.compile(r"^Received SIGINT signal"),
        re.compile(r"^Update id=\d+ is not handled"),
    )

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        return not any(p.search(msg) for p in self._noise)