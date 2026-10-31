from aiogram.types import ReplyKeyboardMarkup, KeyboardButton

def main_menu(is_admin: bool) -> ReplyKeyboardMarkup:
    rows = [
        [KeyboardButton(text="📨 Отправить письмо")],  # верхняя большая
    ]
    bottom = [KeyboardButton(text="📋 Меню")]
    if is_admin:
        bottom.append(KeyboardButton(text="🛠 Админка"))
    rows.append(bottom)

    return ReplyKeyboardMarkup(
        keyboard=rows,
        resize_keyboard=True,
        input_field_placeholder="Выберите действие…",
        selective=True,
    )