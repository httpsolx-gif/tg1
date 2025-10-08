# bot/services/short_client.py
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional

import aiohttp
from common.config import settings

logger = logging.getLogger("bot")


class ShortError(RuntimeError):
    """Raised when URL shortening fails."""


@dataclass(slots=True)
class ShortResult:
    original: str
    short: str


class ShortClient:
    """
    Универсальный шортенер:
    1) Если задан N9_SERVICE_URL — ходит HTTP POST /shorten к локальному n9_service.py
    2) Иначе — пытается использовать локальный модуль n9_adapter (как раньше)
    3) На ошибке — возвращает исходный URL (не ломает флоу отправки)
    """

    def __init__(
        self,
        service_url: Optional[str],
        base_url: Optional[str],
        api_key: Optional[str],
        timeout: int = 10,
    ) -> None:
        self.service_url = (service_url or "").rstrip("/")
        self.base_url = (base_url or "").strip()
        self.api_key = (api_key or "").strip()
        self.timeout = timeout

        # ленивый импорт адаптера, чтобы не требовать его наличия
        self._adapter = None
        if not self.service_url:
            try:
                from bot.services import n9_adapter as _adapter  # type: ignore
            except Exception:
                try:
                    import n9_adapter as _adapter  # type: ignore
                except Exception as e:  # pragma: no cover
                    logger.warning("n9_adapter not available: %s", e)
                    _adapter = None  # type: ignore
            self._adapter = _adapter

    async def _via_service(self, url: str) -> str:
        if not self.service_url:
            raise ShortError("N9_SERVICE_URL is not set")
        post_url = f"{self.service_url}/shorten"
        payload = {"url": url}  # alias/password при желании можно добавить
        timeout = aiohttp.ClientTimeout(total=self.timeout + 2)
        async with aiohttp.ClientSession(timeout=timeout) as s:
            async with s.post(post_url, json=payload) as resp:
                if resp.status >= 400:
                    text = await resp.text()
                    raise ShortError(f"service {resp.status}: {text}")
                data = await resp.json()
                # ожидаем {"status":"OK","short":"https://n9.cl/xxxx"}
                short = (
                    data.get("short")
                    if isinstance(data, dict)
                    else None
                )
                if not (isinstance(short, str) and short.startswith("http")):
                    raise ShortError(f"bad service response: {data!r}")
                return short

    async def _via_adapter(self, url: str) -> str:
        if not self._adapter:
            raise ShortError("Short adapter is not available")

        shorten_fn = getattr(self._adapter, "shorten", None)
        if not shorten_fn:
            raise ShortError("Short adapter has no 'shorten' function")

        async def _run():
            try:
                if asyncio.iscoroutinefunction(shorten_fn):  # async adapter
                    return await shorten_fn(
                        url=url,
                        base_url=self.base_url,
                        api_key=self.api_key,
                        timeout=self.timeout,
                    )
                # sync adapter
                return shorten_fn(
                    url=url,
                    base_url=self.base_url,
                    api_key=self.api_key,
                    timeout=self.timeout,
                )
            except TypeError:
                # запасной сигнатурный вариант: (base_url, api_key, url, timeout)
                if asyncio.iscoroutinefunction(shorten_fn):
                    return await shorten_fn(self.base_url, self.api_key, url, self.timeout)
                return shorten_fn(self.base_url, self.api_key, url, self.timeout)

        return await asyncio.wait_for(_run(), timeout=self.timeout + 2)

    @staticmethod
    def _normalize_adapter_result(res) -> str:
        if isinstance(res, str):
            return res
        if isinstance(res, dict):
            for key in ("short_url", "short", "url"):
                v = res.get(key)
                if isinstance(v, str):
                    return v
        raise ShortError(f"Unexpected adapter response: {res!r}")

    async def shorten(self, url: str) -> ShortResult:
        url = (url or "").strip()
        if not url:
            raise ShortError("Empty URL")

        # 1) попытка через локальный HTTP-сервис (обход без API)
        if self.service_url:
            try:
                short = await self._via_service(url)
                return ShortResult(original=url, short=short)
            except Exception as e:
                logger.error("Shortening via service failed for %s: %s", url, e)
                return ShortResult(original=url, short=url)

        # 2) попытка через адаптер (если сервис не задан)
        if self._adapter:
            try:
                res = await self._via_adapter(url)
                short_url = self._normalize_adapter_result(res)
                if not (isinstance(short_url, str) and short_url.startswith("http")):
                    raise ShortError(f"Adapter returned non-URL: {short_url!r}")
                return ShortResult(original=url, short=short_url)
            except Exception as e:
                logger.error("Shortening via adapter failed for %s: %s", url, e)
                return ShortResult(original=url, short=url)

        # 3) совсем ничего не настроено — no-op
        logger.warning("Shortener disabled; returning original URL")
        return ShortResult(original=url, short=url)


# Singleton
_client: Optional[ShortClient] = None


def get_short_client() -> ShortClient:
    global _client
    if _client is None:
        _client = ShortClient(
            service_url=getattr(settings, "N9_SERVICE_URL", None),
            base_url=getattr(settings, "N9_BASE_URL", None),
            api_key=getattr(settings, "N9_API_KEY", None),
            timeout=getattr(settings, "N9_TIMEOUT", 10),
        )
    return _client


async def shorten_url(url: str) -> str:
    client = get_short_client()
    res = await client.shorten(url)
    return res.short