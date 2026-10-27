"""Замена ведущих юникод-эмодзи на кнопках кастом-эмодзи из пака
«Telegram iOS Icons» (t.me/addemoji/tgiosicons).

Делается централизованно через request-middleware (см. IconRequestMiddleware):
у любой отправляемой клавиатуры ведущий эмодзи кнопки → icon_custom_emoji_id,
а сам эмодзи убирается из текста. Работает, т.к. владелец бота — Premium
(Bot API 9.4, поле icon_custom_emoji_id у Inline/Keyboard-кнопок).

Доступно только с Premium у владельца бота; если эмодзи нет в карте — кнопка
остаётся с обычным юникод-эмодзи.
"""
from __future__ import annotations

import re

# базовый эмодзи (без variation selector) -> custom_emoji_id из пака
EMOJI_ICON = {
    # прямые совпадения с паком + семантические замены (где нет точного).
    # Юникодом остаются: 🔴 🟢 (статус), 🔹 (буллет), 🚀 (лог).
    "⏭": "5895383238473421210",   # ➡️
    "☁️": "6028115612163641653",
    "♻️": "5769248574499983619",   # 🔄
    "⚙️": "5904258298764334001",
    "⛔️": "5938215362473496448",   # 🚫
    "✅": "5774022692642492953",
    "✉️": "6041730074376410123",   # 📥
    "✍️": "6039779802741739617",   # ✏️
    "✖️": "6030757850274336631",   # ❌
    "❌": "6030757850274336631",
    "❗️": "6030563507299160824",
    "⬅️": "5960671702059848143",
    "🌍": "5776233299424843260",   # 🌐
    "🌐": "5776233299424843260",
    "🎯": "6032949275732742941",
    "🏠": "6042137469204303531",
    "🏷": "5888620056551625531",
    "👤": "6032994772321309200",
    "👥": "6032609071373226027",
    "📄": "6034969813032374911",
    "📈": "5938539885907415367",
    "📊": "5936143551854285132",
    "📋": "5920046907782074235",   # 📝
    "📑": "5935831909027290613",   # 🔖
    "📚": "6037286673010660132",   # 📖
    "📤": "6039573425268201570",
    "📥": "6041730074376410123",
    "📦": "5778672437122045013",
    "📧": "6041730074376410123",   # 📥
    "📨": "6041730074376410123",   # 📥
    "📮": "6041730074376410123",   # 📥
    "🔁": "6030657343744644592",
    "🔒": "6037249452824072506",
    "🔗": "6028171274939797252",
    "🖥": "5942734685976138521",
    "🗂": "5766994197705921104",
    "🗑": "6039522349517115015",
    "🗺": "5904650558127478452",
    "🚨": "6039486778597970865",   # 🔔
    "🚫": "5938215362473496448",
    "🛠": "5962952497197748583",   # 🔧
    "🛡": "6030537007350944596",
    "🧩": "5837069325034331827",
    "🧪": "6032850693348399258",   # 🔎
    "🧹": "5811966564039135541",   # 🧽
    "🧽": "5811966564039135541",
}

_VS = "️"


def _norm(e: str) -> str:
    return e.replace(_VS, "")


_NORM_ICON = {_norm(k): v for k, v in EMOJI_ICON.items()}

# ведущий «забег» эмодзи-символов в начале текста
_EMOJI_RUN = re.compile(
    r"^([\U0001F000-\U0001FAFF←-⇿⌀-➿⬀-⯿"
    r"☀-⛿️⃣™ℹ‍]+)"
)


def strip_lead_emoji(text: str) -> str:
    """Убрать ведущий эмодзи-забег и пробел. 'SMTP' для '📧 SMTP'."""
    t = text or ""
    m = _EMOJI_RUN.match(t)
    return t[len(m.group(1)):].lstrip() if m else t


def _enabled() -> bool:
    try:
        from common.config import settings
        return bool(int(getattr(settings, "USE_CUSTOM_EMOJI", 0)))
    except Exception:
        return False


def iconize_markup(markup) -> None:
    """In-place: проставить icon_custom_emoji_id и убрать эмодзи из текста кнопок.

    ТОЛЬКО инлайн-клавиатуры: reply-кнопки кастом-иконки НЕ рендерят (проверено),
    поэтому reply-меню остаётся с юникод-эмодзи. В тексте сообщений иконки даёт
    richify() (как в proxy). Отключается флагом USE_CUSTOM_EMOJI (на неподходящем боте).
    """
    if not _enabled():
        return
    rows = getattr(markup, "inline_keyboard", None)
    if not rows:
        return
    for row in rows:
        for b in row:
            if getattr(b, "icon_custom_emoji_id", None):
                continue
            txt = getattr(b, "text", "") or ""
            m = _EMOJI_RUN.match(txt)
            if not m:
                continue
            run = m.group(1)
            cid = _NORM_ICON.get(_norm(run))
            if not cid:
                continue
            rest = txt[len(run):].lstrip()
            if not rest:
                continue  # кнопка только из эмодзи — оставляем как есть (текст не может быть пустым)
            b.icon_custom_emoji_id = cid
            b.text = rest


# ---- richify: кастом-эмодзи в ТЕКСТЕ сообщений (как в proxy) ----
import json as _json
import os as _os
import re as _re2

_IDS_FILE = _os.path.join(_os.path.dirname(__file__), "..", "..", "tgiosicons_ids.json")


def _load_text_ids() -> dict:
    """Маппинг эмодзи-символ -> custom_emoji_id (для <tg-emoji> в тексте)."""
    try:
        if _os.path.exists(_IDS_FILE):
            with open(_IDS_FILE, encoding="utf-8") as f:
                return {str(k): str(v) for k, v in _json.load(f).items()}
    except Exception:
        pass
    # фолбэк — хотя бы кнопочная карта
    return dict(EMOJI_ICON)


CUSTOM_IDS = _load_text_ids()
# длинные ключи первыми — чтобы составные эмодзи (с VS16) заменялись целиком
_CUSTOM_ITEMS = sorted(CUSTOM_IDS.items(), key=lambda kv: len(kv[0]), reverse=True)

RAW_MARKER = "⁠"  # WORD JOINER: текст с этим префиксом richify не трогает


def raw(text: str) -> str:
    return RAW_MARKER + text


def richify(text: str) -> str:
    """Заменяет юникод-эмодзи в тексте на <tg-emoji> из пака. Идемпотентна."""
    if not text:
        return text
    if text.startswith(RAW_MARKER):
        return text[len(RAW_MARKER):]
    if not _enabled() or not _CUSTOM_ITEMS or "<tg-emoji" in text:
        return text
    for char, cid in _CUSTOM_ITEMS:
        if char in text:
            text = text.replace(char, f'<tg-emoji emoji-id="{cid}">{char}</tg-emoji>')
    return text


# ---- request middlewares ----
try:
    from aiogram.client.session.middlewares.base import BaseRequestMiddleware

    class IconRequestMiddleware(BaseRequestMiddleware):
        """Инлайн-кнопки: ведущий эмодзи -> icon_custom_emoji_id."""
        async def __call__(self, make_request, bot, method):
            rm = getattr(method, "reply_markup", None)
            if rm is not None:
                try:
                    iconize_markup(rm)
                except Exception:
                    pass
            return await make_request(bot, method)

    class RichifyRequestMiddleware(BaseRequestMiddleware):
        """Текст/подпись сообщений: юникод-эмодзи -> <tg-emoji> (parse_mode=HTML)."""
        async def __call__(self, make_request, bot, method):
            try:
                updates = {}
                txt = getattr(method, "text", None)
                if isinstance(txt, str):
                    new = richify(txt)
                    if new != txt:
                        updates["text"] = new
                cap = getattr(method, "caption", None)
                if isinstance(cap, str):
                    new = richify(cap)
                    if new != cap:
                        updates["caption"] = new
                if updates:
                    method = method.model_copy(update=updates)
            except Exception:
                pass
            return await make_request(bot, method)
except Exception:  # pragma: no cover
    IconRequestMiddleware = None  # type: ignore
    RichifyRequestMiddleware = None  # type: ignore
