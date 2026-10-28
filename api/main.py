"""Точка входа API.

Хардненинг (раздел 8 api/README.md): слушаем ТОЛЬКО 127.0.0.1, reload выключен.
Наружу — исключительно через Cloudflare/nginx (TLS, лимит тела, таймауты).
Напрямую в интернет (0.0.0.0) поднимать нельзя: X-Forwarded-For станет подделываемым
и IP-whitelist/аудит перестанут иметь смысл.

Хост/порт можно переопределить через env API_HOST / API_PORT (для контейнера за
прокси). По умолчанию — локальный интерфейс.
"""
import os

import uvicorn

from api.app import build_app

app = build_app()

if __name__ == "__main__":
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8080"))
    uvicorn.run("api.main:app", host=host, port=port, reload=False, proxy_headers=True)
