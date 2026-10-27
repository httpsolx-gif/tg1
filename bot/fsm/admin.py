from aiogram.fsm.state import State, StatesGroup

# --- Admin FSM states ---

class SmtpAdd(StatesGroup):
    """Добавление SMTP вручную: ждём строку или .txt файл."""
    waiting_input = State()


class SmtpAutoAdd(StatesGroup):
    """Загрузка SMTP в автопул: ждём строку или .txt файл."""
    waiting_input = State()


class NodeAdd(StatesGroup):
    """Добавление нод: ждём строки `login ip pass` или .txt файл, затем гео/регион AWS."""
    waiting_input = State()
    waiting_region = State()


class NodeEdit(StatesGroup):
    """Редактирование ноды из списка: смена региона."""
    region = State()


class TemplateUpload(StatesGroup):
    """Загрузка шаблонов: кидаем .html файлы, имя решает service/mailbox."""
    waiting = State()


class RubricAdd(StatesGroup):
    """Создание рубрики: ждём название."""
    name = State()


class RubricEdit(StatesGroup):
    """Редактирование полей рубрики (имя/тайтл/отправитель)."""
    value = State()


class MailboxAdd(StatesGroup):
    """Добавление кастомного типа почты: ждём `код имя`."""
    waiting = State()


class MailboxDomains(StatesGroup):
    """Ввод доменов для типа почты (gmx.de web.de …)."""
    waiting = State()


class ServiceCfgEdit(StatesGroup):
    """Редактирование sender/title для пары (сервис, рубрика)."""
    value = State()


class AwsAdd(StatesGroup):
    """Добавление AWS SES: ждём строки region:accesskey:secretkey:emailfrom."""
    waiting = State()


class SmtpLimit(StatesGroup):
    """Ввод числового лимита отправок (на аккаунт или группу)."""
    value = State()
