from fastapi import FastAPI

from app.config import get_settings
from app.health import router as health_router


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title=s.app_name, version=s.app_version)
    app.include_router(health_router)
    return app


app = create_app()
