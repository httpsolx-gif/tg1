from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

ADMIN_BUTTONS = [
    ["🧩 SRVS",  "📊 STATS", "👥 USERS"],
    ["🛡 API",   "🧪 TEST",  "🚨 ALERT"],
    ["🖥 NODES", "💾 BACKUP", "📦 OTHER"],
]

# Разделы для ИНЛАЙН-меню. Убраны из меню: MAIL, TEMPL (в рубрики), AWS (внутрь NODES),
# SPAM (внутрь TEST), BC (внутрь ALERT), SMTP и SHORT (п.82: вместо них 📦 OTHER, пока пусто;
# хендлеры smtp/short живы — вернуть в OTHER, когда понадобятся).
ADMIN_SECTIONS = [
    ("🧩 SRVS", "srvs"), ("📊 STATS", "stats"), ("👥 USERS", "users"), ("🛡 API", "api"),
    ("🧪 TEST", "test"), ("🚨 ALERT", "alert"), ("🖥 NODES", "nodes"), ("💾 BACKUP", "backup"),
    ("📦 OTHER", "other"),
]


def admin_menu_inline() -> InlineKeyboardMarkup:
    rows, row = [], []
    for label, key in ADMIN_SECTIONS:
        row.append(InlineKeyboardButton(text=label, callback_data=f"adm:{key}"))
        if len(row) == 3:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_menu() -> ReplyKeyboardMarkup:
    rows = []
    # Основные разделы (строки по 3 кнопки)
    for row in ADMIN_BUTTONS:
        rows.append([KeyboardButton(text=txt) for txt in row])
    # «Назад» отдельной строкой
    rows.append([KeyboardButton(text="⬅️ Назад")])

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Админка — выбери раздел…",
        selective=True,
        one_time_keyboard=False,
        is_persistent=True,
    )