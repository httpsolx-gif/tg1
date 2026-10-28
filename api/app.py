"""Сборка FastAPI-приложения.

Хардненинг (раздел 8 api/README.md): по умолчанию закрыты /docs, /redoc и
/openapi.json (публичный API не раскрывает схему). Для локальной отладки включить
через env API_DOCS=1. Фоновый воркер отправки запускается/останавливается через
lifespan (см. api/worker.py).
"""
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from api.routes.redirect import router as redirect_router
from api.routes.logs import router as logs_router
from api.routes.send import router as send_router
from api import worker


@asynccontextmanager
async def _lifespan(app: FastAPI):
    await worker.start()
    try:
        yield
    finally:
        await worker.stop()


def build_app() -> FastAPI:
    docs_on = os.getenv("API_DOCS", "0") == "1"
    app = FastAPI(
        title="Mailer API",
        docs_url="/docs" if docs_on else None,
        redoc_url="/redoc" if docs_on else None,
        openapi_url="/openapi.json" if docs_on else None,
        lifespan=_lifespan,
    )

    @app.middleware("http")
    async def _audit(request, call_next):
        """Аудит каждого вызова /api/* (п.47): токен/IP/путь/статус. Ошибки глушим."""
        response = await call_next(request)
        try:
            path = request.url.path
            if path.startswith("/api/"):
                from api.auth import _client_ip
                from bot.services import api_tokens as T, api_audit
                tok_id = None
                prefix = None
                auth = request.headers.get("authorization", "")
                if auth.startswith("Bearer "):
                    raw = auth.split(" ", 1)[1].strip()
                    prefix = raw[:14]
                    t = T.lookup(raw)             # None, если токен не распознан/отозван
                    tok_id = t.id if t else None
                api_audit.record(tok_id, prefix, _client_ip(request),
                                 request.method, path, response.status_code)
        except Exception:
            pass
        return response

    app.include_router(redirect_router)
    app.include_router(logs_router)
    app.include_router(send_router)
    return app
