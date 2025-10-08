from typing import List, Dict, Tuple
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# Try to import central registries; fall back to empty if not present
try:
    from common.constants import SERVICES, COUNTRIES, COUNTRY_SERVICES  # type: ignore
except Exception:
    SERVICES: List[str] = []
    COUNTRIES: Dict[str, Tuple[str, str]] = {}
    COUNTRY_SERVICES: Dict[str, List[str]] = {}

# ---------- helpers ----------
def _btn(text: str, cb: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=cb)

def _mk(rows: List[List[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)

def _chunk(items: List[InlineKeyboardButton], size: int) -> List[List[InlineKeyboardButton]]:
    return [items[i:i + size] for i in range(0, len(items), size)]

# ---------- ROOT ----------
def kb_short_root() -> InlineKeyboardMarkup:
    """
    Корень раздела Short.
    """
    rows = [
        [_btn("⚙️ Settings", "short:settings")],
        [_btn("🧹 Clear", "short:clear")],
        [_btn("⬅️ Назад", "admin:back")],
    ]
    return _mk(rows)

# ---------- SETTINGS L1: target (gmail/main) ----------
def kb_short_settings_targets() -> InlineKeyboardMarkup:
    """
    Первый шаг настроек: выбор таргета (gmail | main).
    """
    rows = [
        [_btn("📮 Gmail", "short:settings:target:gmail")],
        [_btn("📦 Main", "short:settings:target:main")],
        [_btn("⬅️ Назад", "short:back")],
    ]
    return _mk(rows)

# Backward compatible name expected by some handlers
def kb_short_target_picker() -> InlineKeyboardMarkup:
    return kb_short_settings_targets()

# ---------- SETTINGS L2: scope (global | countries) ----------
def kb_short_settings_scope(target: str) -> InlineKeyboardMarkup:
    """
    Второй шаг: область применения выбора шортнера.
    """
    rows = [
        [_btn("🌍 Global", f"short:settings:scope:{target}:global")],
        [_btn("🗺 По странам", f"short:settings:scope:{target}:countries")],
        [_btn("⬅️ Назад", "short:settings")],
    ]
    return _mk(rows)

# ---------- SETTINGS L3a: countries list ----------
def kb_short_country_list(target: str) -> InlineKeyboardMarkup:
    """
    Список стран (3 в ряд), для выбора конкретной страны.
    """
    rows: List[List[InlineKeyboardButton]] = []
    # COUNTRIES is Dict[code] -> (name, flag)
    buttons: List[InlineKeyboardButton] = []
    for code, (name, flag) in sorted(COUNTRIES.items(), key=lambda kv: kv[1][0].lower()):
        buttons.append(_btn(f"{flag} {name}", f"short:settings:country:{target}:{code}"))
    rows.extend(_chunk(buttons, 3))
    rows.append([_btn("⬅️ Назад", f"short:settings:target:{target}")])
    return _mk(rows)

# ---------- SETTINGS L3b: services by country ----------
def kb_short_service_list_for_country(target: str, country_code: str) -> InlineKeyboardMarkup:
    """
    Список сервисов выбранной страны.
    """
    rows: List[List[InlineKeyboardButton]] = []
    services = COUNTRY_SERVICES.get(country_code, [])
    if not services:
        rows.append([_btn("❌ Нет сервисов", "short:noop")])
    else:
        for svc in services:
            rows.append([_btn(f"🔹 {svc}", f"short:settings:service:{target}:{country_code}:{svc}")])
    rows.append([_btn("⬅️ Назад", f"short:settings:country:{target}:{country_code}:back")])
    return _mk(rows)

# ---------- CLEAR ----------
def kb_short_clear_menu() -> InlineKeyboardMarkup:
    """
    Меню очистки: выборочно / неиспользуемые.
    """
    rows = [
        [_btn("🗑 Выборочно", "short:clear:pick")],
        [_btn("🧹 Неиспользуемые", "short:clear:unused")],
        [_btn("⬅️ Назад", "short:back")],
    ]
    return _mk(rows)

def kb_short_clear_pick(emails: List[str]) -> InlineKeyboardMarkup:
    """
    Список доменов/правил для выборочной очистки (по email/домену).
    """
    rows: List[List[InlineKeyboardButton]] = []
    for em in emails:
        rows.append([_btn(em, f"short:clear:item:{em}")])
    rows.append([_btn("⬅️ Назад", "short:clear")])
    return _mk(rows)

# ---------- SETTINGS L4: provider picker ----------
def kb_short_provider_picker(
    target: str,
    scope: str,
    *,
    country: str | None = None,
    service: str | None = None,
    providers: list[str] | None = None,
    current: str | None = None,
) -> InlineKeyboardMarkup:
    """
    Выбор шортнера (напр. ['n9.cl', 'is.gd', ...]).
    scope: 'global' | 'countries' | 'country' | 'service'
    country/service — передавать по ситуации (для back/callback).
    """
    rows: List[List[InlineKeyboardButton]] = []

    providers = providers or ["n9.cl"]  # минимум, чтобы меню не было пустым
    for p in providers:
        label = f"✅ {p}" if current and p == current else p
        cc = country or "-"
        sv = service or "-"
        cb = f"short:settings:provider:{target}:{scope}:{cc}:{sv}:{p}"
        rows.append([_btn(label, cb)])

    # back
    if scope in {"global"}:
        rows.append([_btn("⬅️ Назад", f"short:settings:scope:{target}:global")])
    elif scope in {"countries", "country"} and country:
        # если мы в списке стран, возвращаемся к выбору страны
        rows.append([_btn("⬅️ Назад", f"short:settings:country:{target}:{country}")])
    elif scope == "service" and country:
        rows.append([_btn("⬅️ Назад", f"short:settings:service:{target}:{country}:{service or '-'}")])
    else:
        rows.append([_btn("⬅️ Назад", f"short:settings:target:{target}")])

    return _mk(rows)

# Для совместимости, если где-то ожидали старое имя
kb_short_target_picker = kb_short_settings_targets