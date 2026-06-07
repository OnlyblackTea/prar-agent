from fastapi import FastAPI

from app.api.adapters import router as adapters_router
from app.api.comments import router as comments_router
from app.api.providers import router as providers_router
from app.api.sessions import router as sessions_router
from app.api.ws_plan import router as ws_plan_router
from app.config import get_settings
from app.core.logging import RequestContextMiddleware, setup_logging
from app.health import router as health_router


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(
        log_level=s.log_level,
        json_format=s.log_format == "json" or s.environment == "production",
    )
    app = FastAPI(title=s.app_name, version=s.app_version)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(providers_router)
    app.include_router(adapters_router)
    app.include_router(sessions_router)
    app.include_router(comments_router)
    app.include_router(ws_plan_router)
    return app


app = create_app()
