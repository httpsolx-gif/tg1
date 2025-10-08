from fastapi import FastAPI
from api.routes.redirect import router as redirect_router
from api.routes.logs import router as logs_router

def build_app() -> FastAPI:
    app = FastAPI(title="Mailer API")
    app.include_router(redirect_router)
    app.include_router(logs_router)
    return app
