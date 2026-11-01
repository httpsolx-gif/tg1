import logging, asyncio, httpx
from common.tgids import norm_chat_id

class TelegramChannelHandler(logging.Handler):
    """channel_id — строка или вид канала из common.channels ('err', 'send', 'switch'):
    тогда id берётся при каждой записи (задаётся в боте, кэш 30 с)."""

    def __init__(self, bot_token: str, channel_id: str, level=logging.NOTSET, timeout: float = 5.0,
                 kind: str | None = None):
        super().__init__(level=level)
        self.bot_token = bot_token
        self.channel_id = norm_chat_id(channel_id)  # «100…» без «-» → «-100…»
        self.kind = kind
        self.timeout = timeout

    def _chat(self) -> str:
        if self.kind:
            from common import channels
            return channels.get(self.kind)
        return self.channel_id

    def emit(self, record: logging.LogRecord) -> None:
        try:
            chat = self._chat()
            if not chat:
                return
            text = self.format(record)
            asyncio.get_running_loop().create_task(self._send(chat, text[:4000]))
        except Exception:
            pass

    async def _send(self, chat: str, text: str):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                await client.post(url, json=payload)
            except Exception:
                pass
