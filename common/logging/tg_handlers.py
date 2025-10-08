import logging, asyncio, httpx

class TelegramChannelHandler(logging.Handler):
    def __init__(self, bot_token: str, channel_id: str, level=logging.NOTSET, timeout: float = 5.0):
        super().__init__(level=level)
        self.bot_token = bot_token
        self.channel_id = channel_id
        self.timeout = timeout

    def emit(self, record: logging.LogRecord) -> None:
        if not self.channel_id:
            return
        try:
            text = self.format(record)
            asyncio.create_task(self._send(text))
        except Exception:
            pass

    async def _send(self, text: str):
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = {"chat_id": self.channel_id, "text": text, "disable_web_page_preview": True}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                await client.post(url, json=payload)
            except Exception:
                pass
