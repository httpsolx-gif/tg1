from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

ADMIN_BUTTONS = [
    ["📧 SMTP",  "🔗 SHORT", "🧩 SRVS",  "📊 STATS"],
    ["👥 USERS", "🛡 API",   "🧪 TEST",  "🚨 ALERT"],
]

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