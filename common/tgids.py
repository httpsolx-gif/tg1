"""Нормализация chat_id каналов из .env.

В .env id часто вставляют без ведущего «-» (как отдаёт getChat: 1002508588680).
Для каналов/супергрупп Telegram ждёт «-100…», иначе sendMessage → 400 «chat not found»,
а наши хендлеры/лупы ошибку глотают — канал молчит без видимой причины.

Правило: @username и уже «-…» оставляем как есть; чисто числовой id, начинающийся
со «100» (внутренний id супергруппы/канала), получает ведущий «-». Остальные числа
(личные chat_id и т.п.) не трогаем.
"""
from __future__ import annotations


def norm_chat_id(raw: str) -> str:
    s = (raw or "").strip()
    if not s or s.startswith("@") or s.startswith("-"):
        return s
    if s.isdigit() and s.startswith("100"):
        return "-" + s
    return s
