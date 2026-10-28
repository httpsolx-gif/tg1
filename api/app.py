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
    app.include_router(redirect_router)
    app.include_router(logs_router)
    app.include_router(send_router)
    return app
