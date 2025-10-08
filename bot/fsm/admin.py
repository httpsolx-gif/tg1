from aiogram.fsm.state import State, StatesGroup

# --- Admin FSM states ---

class SmtpAdd(StatesGroup):
    """Добавление SMTP вручную: ждём строку или .txt файл."""
    waiting_input = State()


class SmtpAutoAdd(StatesGroup):
    """Загрузка SMTP в автопул: ждём строку или .txt файл."""
    waiting_input = State()
