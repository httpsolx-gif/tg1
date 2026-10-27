from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardMarkup, InlineKeyboardButton

ADMIN_BUTTONS = [
    ["📧 SMTP",  "🔗 SHORT", "🧩 SRVS",  "📊 STATS"],
    ["👥 USERS", "🛡 API",   "🧪 TEST",  "🚨 ALERT"],
    ["🖥 NODES"],
]

# Разделы для ИНЛАЙН-меню. Убраны из меню: MAIL, TEMPL (в рубрики), AWS (внутрь NODES).
ADMIN_SECTIONS = [
    ("📧 SMTP", "smtp"), ("🔗 SHORT", "short"), ("🧩 SRVS", "srvs"), ("📊 STATS", "stats"),
    ("👥 USERS", "users"), ("🛡 API", "api"), ("🧪 TEST", "test"), ("🚨 ALERT", "alert"),
    ("🖥 NODES", "nodes"),
]


def admin_menu_inline() -> InlineKeyboardMarkup:
    rows, row = [], []
    for label, key in ADMIN_SECTIONS:
        row.append(InlineKeyboardButton(text=label, callback_data=f"adm:{key}"))
        if len(row) == 4:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="adm:back")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def admin_menu() -> ReplyKeyboardMarkup:
    rows = []
    # Основные разделы (2 строки × 4 кнопки)
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