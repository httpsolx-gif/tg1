from typing import List
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Централизованные справочники стран/сервисов.
# Если common.constants недоступен — используем небольшой фолбэк, чтобы меню не падало.
try:
    from common.constants import COUNTRIES, COUNTRY_FLAGS, COUNTRY_SERVICES  # type: ignore
    _COUNTRIES: List[tuple[str, str]] = [(code, COUNTRY_FLAGS.get(code, "")) for code in COUNTRIES]
except Exception:  # pragma: no cover
    _COUNTRIES = [("PL", "🇵🇱"), ("DE", "🇩🇪"), ("CZ", "🇨🇿")]
    COUNTRY_SERVICES = {"PL": ["myemail.pl"], "DE": ["myemail.de"], "CZ": ["myemail.cz"]}

# Доступные сокращалки (провайдеры).
SHORT_PROVIDERS: List[str] = ["n9.cl"]


# ---------- helpers ----------
def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)


def _mk(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _chunk(items: List[InlineKeyboardButton], size: int) -> List[List[InlineKeyboardButton]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


# ---------- ROOT ----------
def kb_short_root() -> InlineKeyboardMarkup:
    """Корень раздела Short. «Назад» выходит в админку."""
    return _mk([
        [_btn("🌐 Личные домены", "sdom:list")],
        [_btn("⚙️ Settings", "short:settings")],
        [_btn("🧹 Clear", "short:clear")],
        [_btn("⬅️ Назад", "admin:back")],
    ])


# ---------- SETTINGS L1: target (gmail/main) ----------
def kb_short_targets() -> InlineKeyboardMarkup:
    """Шаг 1 настроек: выбор таргета. «Назад» → корень Short."""
    return _mk([
        [_btn("📮 Gmail", "short:t:gmail")],
        [_btn("📦 Main", "short:t:main")],
        [_btn("⬅️ Назад", "short:back")],
    ])


# ---------- SETTINGS L2: scope (global | countries) ----------
def kb_short_scope(target: str) -> InlineKeyboardMarkup:
    """Шаг 2: область применения. «Назад» → выбор таргета."""
    return _mk([
        [_btn("🌍 Global", f"short:sc:{target}:global")],
        [_btn("🗺 По странам", f"short:sc:{target}:countries")],
        [_btn("⬅️ Назад", "short:settings")],
    ])


# ---------- SETTINGS L3a: countries list ----------
def kb_short_countries(target: str) -> InlineKeyboardMarkup:
    """Список стран (3 в ряд). «Назад» → выбор области."""
    buttons = [_btn(f"{flag} {code}", f"short:c:{target}:{code}") for code, flag in _COUNTRIES]
    rows = _chunk(buttons, 3)
    rows.append([_btn("⬅️ Назад", f"short:t:{target}")])
    return _mk(rows)


# ---------- SETTINGS L3b: services by country ----------
def kb_short_services(target: str, country_code: str) -> InlineKeyboardMarkup:
    """Список сервисов страны. «Назад» → список стран."""
    services = COUNTRY_SERVICES.get(country_code, [])
    rows: List[List[InlineKeyboardButton]] = []
    if services:
        for svc in services:
            rows.append([_btn(f"🔹 {svc}", f"short:sv:{target}:{country_code}:{svc}")])
    else:
        rows.append([_btn("❌ Нет сервисов", "short:noop")])
    rows.append([_btn("⬅️ Назад", f"short:sc:{target}:countries")])
    return _mk(rows)


# ---------- SETTINGS L4: provider picker ----------
def kb_short_providers(
    target: str,
    scope: str,
    *,
    country: str = "-",
    service: str = "-",
    current: str | None = None,
) -> InlineKeyboardMarkup:
    """
    Выбор сокращалки.
    scope: 'global' | 'country' | 'service'.
    «Назад» ведёт на экран, с которого пришли.
    """
    rows: List[List[InlineKeyboardButton]] = []
    for p in SHORT_PROVIDERS:
        label = f"✅ {p}" if current and p == current else p
        rows.append([_btn(label, f"short:p:{target}:{scope}:{country}:{service}:{p}")])

    if scope == "service" and country != "-":
        back = f"short:c:{target}:{country}"
    elif scope == "country" and country != "-":
        back = f"short:sc:{target}:countries"
    else:  # global
        back = f"short:t:{target}"
    rows.append([_btn("⬅️ Назад", back)])
    return _mk(rows)


# ---------- CLEAR ----------
def kb_short_clear() -> InlineKeyboardMarkup:
    """Меню очистки. «Назад» → корень Short."""
    return _mk([
        [_btn("🗑 Выборочно", "short:clear:pick")],
        [_btn("🧹 Неиспользуемые", "short:clear:unused")],
        [_btn("⬅️ Назад", "short:root")],
    ])


def kb_short_back_to(cb: str, text: str = "⬅️ Назад") -> InlineKeyboardMarkup:
    """Одиночная кнопка «Назад» на произвольный экран."""
    return _mk([[_btn(text, cb)]])
