# bot/middlewares/error_report.py
from aiogram import BaseMiddleware
from aiogram.types import Update, Message, CallbackQuery
from typing import Callable, Dict, Any, Awaitable
import logging
import traceback

log = logging.getLogger("errors")  # отдельный “тихий” логгер

class ErrorReportMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Update, Dict[str, Any]], Awaitable[Any]],
        event: Update,
        data: Dict[str, Any]
    ) -> Any:
        try:
            return await handler(event, data)
        except Exception as e:
            # собираем максимум контекста, но без лишнего шума
            payload: Dict[str, Any] = {
                "err": str(e),
                "exc_type": e.__class__.__name__,
                "traceback": "".join(traceback.format_exc()),
            }

            # FSM-состояние (если есть)
            state = data.get("state")
            if state:
                try:
                    payload["fsm_state"] = await state.get_state()
                    payload["fsm_data"] = await state.get_data()
                except Exception:
                    pass

            # Сообщение
            if isinstance(event, Message):
                m: Message = event
                payload.update({
                    "kind": "message",
                    "from_id": getattr(m.from_user, "id", None),
                    "chat_id": getattr(m.chat, "id", None),
                    "text": m.text,
                })

            # Callback
            elif isinstance(event, CallbackQuery):
                c: CallbackQuery = event
                payload.update({
                    "kind": "callback",
                    "from_id": getattr(c.from_user, "id", None),
                    "chat_id": getattr(getattr(c.message, "chat", None), "id", None) if c.message else None,
                    "data": c.data,
                })

            else:
                payload["kind"] = type(event).__name__

            # Пишем в отдельный логгер “errors” одним JSON-объектом
            log.error("bot-exception", extra={"payload": payload})

            # чтобы пользователь не зависал — короткий answer/notify
            try:
                if isinstance(event, CallbackQuery):
                    await event.answer("Ошибка обработчика. Сообщение записано в лог.", show_alert=False)
            except Exception:
                pass

            # важно проглотить исключение (чтоб aiogram не дублировал в общий лог)
            return None