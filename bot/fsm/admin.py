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


class RubricMailboxAdd(StatesGroup):
    """Почты рубрики: ждём список через запятую (web, gmx, gmail.com …)."""
    waiting = State()


class ScopeInput(StatesGroup):
    """Области SMTP/SHORT: загрузка SMTP-базы (текст/.txt) или ввод лимита."""
    smtp = State()
    limit = State()


class MailboxAdd(StatesGroup):
    """Добавление кастомного типа почты: ждём `код имя`."""
    waiting = State()


class MailboxDomains(StatesGroup):
    """Ввод доменов для типа почты (gmx.de web.de …)."""
    waiting = State()


class ServiceCfgEdit(StatesGroup):
    """Редактирование sender/title для пары (сервис, рубрика)."""
    value = State()


class SmtpLimit(StatesGroup):
    """Ввод числового лимита отправок (на аккаунт или группу)."""
    value = State()


class ShortDomainAdd(StatesGroup):
    """Добавление личного short-домена: домен → CF токен → e-mail → токен регистратора → нода."""
    domain = State()
    token = State()
    email = State()
    registrar = State()
    node = State()


class AccessAdd(StatesGroup):
    """Добавление в список доступа (ban/white/black): ждём id/@username (пачкой). data: lst."""
    waiting = State()


class UserFlow(StatesGroup):
    """USERS: ввод id/@username для действия (ban/white/black/unban; data: mode) и для поиска карточки."""
    act = State()
    search = State()
