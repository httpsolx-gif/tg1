from typing import List, Dict, Tuple

from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Централизованные списки сервисов/стран
# Если common.constants ещё не заполнен — падать не будем.
try:
    from common.constants import SERVICES, COUNTRIES, COUNTRY_SERVICES  # type: ignore
except Exception:
    SERVICES: List[str] = []
    COUNTRIES: Dict[str, Tuple[str, str]] = {}
    COUNTRY_SERVICES: Dict[str, List[str]] = {}

def _chunk(items: List[InlineKeyboardButton], size: int) -> List[List[InlineKeyboardButton]]:
    return [items[i:i + size] for i in range(0, len(items), size)]

# ---------- Базовые элементы ----------

def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)

def _row(*buttons: InlineKeyboardButton) -> List[InlineKeyboardButton]:
    return list(buttons)

def _mk(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)

def kb_back(cb: str = "admin:back", text: str = "⬅️ Назад") -> InlineKeyboardMarkup:
    return _mk([[_btn(text, cb)]])

# ---------- Корневое меню SMTP ----------

def kb_smtp_root() -> InlineKeyboardMarkup:
    """
    Корневое меню раздела SMTP.
    """
    rows = [
        [
            _btn("➕ Add", "smtp:add"),
            _btn("📥 Add Auto", "smtp:add_auto"),
        ],
        [
            _btn("🧹 Clear", "smtp:clear"),
            _btn("⚙️ Settings", "smtp:settings"),
        ],
        [
            _btn("🔁 Auto", "smtp:auto"),
            _btn("⏱ Limit", "smtp:limit"),
        ],
        [
            _btn("🚨 Forced change", "smtp:force"),
        ],
        [
            _btn("⬅️ Назад", "admin:back"),
        ],
    ]
    return _mk(rows)

# ---------- Списки стран / сервисов ----------

def kb_country_list(prefix: str = "smtp:country", with_global: bool = True) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора страны.
    :param prefix: префикс в callback_data
    :param with_global: Добавлять ли кнопку '🌍 Global'
    """
    rows: List[List[InlineKeyboardButton]] = []

    if with_global:
        rows.append([_btn("🌍 Global", f"{prefix}:global")])

    # COUNTRIES: Dict[code, (name, flag)]
    # отсортируем по отображаемому названию
    country_buttons: List[InlineKeyboardButton] = []
    for code, (name, flag) in sorted(COUNTRIES.items(), key=lambda kv: kv[1][0].lower()):
        country_buttons.append(_btn(f"{flag} {name}", f"{prefix}:{code}"))

    # раскладываем по 3 в ряд
    rows.extend(_chunk(country_buttons, 3))

    rows.append([_btn("⬅️ Назад", "smtp:back")])
    return _mk(rows)

def kb_service_list(prefix: str = "service", page: int = 0, per_page: int = 20, with_all: bool = True, services: List[str] | None = None) -> InlineKeyboardMarkup:
    """
    Клавиатура выбора сервиса (пагинация).
    :param prefix: префикс в callback_data
    :param page: страница
    :param per_page: элементов на страницу
    :param with_all: добавлять кнопку 'Вся страна' / 'Все сервисы'
    """
    data = services if services is not None else SERVICES
    total = len(data)
    start = page * per_page
    end = min(start + per_page, total)

    rows: List[List[InlineKeyboardButton]] = []

    if with_all:
        rows.append([_btn("🧩 Все сервисы", f"{prefix}:__all__")])

    for svc in data[start:end]:
        rows.append([_btn(f"🔹 {svc}", f"{prefix}:{svc}")])

    # Пагинация (если нужно)
    nav: List[InlineKeyboardButton] = []
    if start > 0:
        nav.append(_btn("⬅️ Prev", f"{prefix}:__page__:{page-1}"))
    if end < total:
        nav.append(_btn("Next ➡️", f"{prefix}:__page__:{page+1}"))
    if nav:
        rows.append(nav)

    rows.append([_btn("⬅️ Назад", "smtp:back")])
    return _mk(rows)

# ---------- Универсальные подменю для операций ----------

def kb_clear_menu() -> InlineKeyboardMarkup:
    """
    Клавиатура для Clear: выборочно / неиспользуемые
    """
    rows = [
        [_btn("🗑 Выборочно", "smtp:clear:pick")],
        [_btn("🧹 Неиспользуемые", "smtp:clear:unused")],
        [_btn("⬅️ Назад", "smtp:back")],
    ]
    return _mk(rows)

def kb_settings_menu() -> InlineKeyboardMarkup:
    """
    Клавиатура Settings: Gmail/Main
    """
    rows = [
        [_btn("📮 Gmail", "smtp:set:gmail")],
        [_btn("📦 Main", "smtp:set:main")],
        [_btn("⬅️ Назад", "smtp:back")],
    ]
    return _mk(rows)

def kb_auto_menu() -> InlineKeyboardMarkup:
    """
    Клавиатура Auto: включение/выключение по global и странам
    """
    rows = [
        [_btn("🌍 Global", "smtp:auto:global")],
        [_btn("🗺 По странам", "smtp:auto:countries")],
        [_btn("⬅️ Назад", "smtp:back")],
    ]
    return _mk(rows)

def kb_force_menu() -> InlineKeyboardMarkup:
    """
    Клавиатура Forced change: Gmail/Main
    """
    rows = [
        [_btn("📮 Gmail", "smtp:force:gmail")],
        [_btn("📦 Main", "smtp:force:main")],
        [_btn("⬅️ Назад", "smtp:back")],
    ]
    return _mk(rows)

def kb_limit_menu() -> InlineKeyboardMarkup:
    """
    Клавиатура Limit: Gmail/Main
    """
    rows = [
        [_btn("📮 Gmail", "smtp:limit:gmail")],
        [_btn("📦 Main", "smtp:limit:main")],
        [_btn("⬅️ Назад", "smtp:back")],
    ]
    return _mk(rows)
def kb_service_list_for_country(country_code: str, prefix: str = "smtp:service") -> InlineKeyboardMarkup:
    """
    Клавиатура сервисов для конкретной страны.
    :param country_code: ISO-код страны
    :param prefix: префикс callback_data
    """
    services = COUNTRY_SERVICES.get(country_code, [])
    rows: List[List[InlineKeyboardButton]] = []

    if services:
        for svc in services:
            rows.append([_btn(f"🔹 {svc}", f"{prefix}:{country_code}:{svc}")])
    else:
        rows.append([_btn("❌ Нет сервисов", f"{prefix}:{country_code}:__none__")])

    rows.append([_btn("⬅️ Назад", "smtp:countries")])
    return _mk(rows)