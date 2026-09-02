import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.adapters import router as adapters_router
from app.api.comments import router as comments_router
from app.api.memories import router as memories_router
from app.api.providers import router as providers_router
from app.api.sessions import router as sessions_router
from app.api.ws_act import router as ws_act_router
from app.api.ws_plan import get_router
from app.api.ws_plan import router as ws_plan_router
from app.config import get_settings
from app.core.embedding import get_embedding_service
from app.core.logging import RequestContextMiddleware, get_logger, setup_logging
from app.db.session import get_sessionmaker
from app.health import router as health_router
from app.memory.consolidator import (
    Consolidator,
    NoDefaultAdapterError,
    consolidator_loop,
    resolve_default_adapter,
)
from app.services.memory_service import MemoryService

_log = get_logger("main")


async def _run_consolidator_cycle() -> None:
    """后台循环单轮：异常不外抛（日志不静默），事务失败即回滚，下轮重试。"""
    try:
        async with get_sessionmaker()() as db:
            adapter = await resolve_default_adapter(db)
            consolidator = Consolidator(
                db=db,
                store=MemoryService(db, get_embedding_service()),
                router=get_router(),
                embedding=get_embedding_service(),
            )
            result = await consolidator.run_once(adapter=adapter)
            await db.commit()
            _log.info(
                "consolidator_cycle",
                processed=result.processed,
                distilled=result.distilled,
                inserted=result.inserted,
                merged=result.merged,
                decayed=result.decayed,
            )
    except NoDefaultAdapterError:
        _log.warning("consolidator_cycle_skipped", reason="no_default_adapter")
    except Exception:
        _log.exception("consolidator_cycle_failed")


async def _background_consolidator(interval_seconds: float) -> None:
    await _run_consolidator_cycle()  # 启动即跑一轮，消化停机积压
    await consolidator_loop(
        interval=interval_seconds, run_cycle=_run_consolidator_cycle,
    )


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    task = asyncio.create_task(
        _background_consolidator(get_settings().consolidator_interval_seconds)
    )
    yield
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


def create_app() -> FastAPI:
    s = get_settings()
    setup_logging(
        log_level=s.log_level,
        json_format=s.log_format == "json" or s.environment == "production",
    )
    app = FastAPI(title=s.app_name, version=s.app_version, lifespan=_lifespan)
    app.add_middleware(RequestContextMiddleware)
    app.include_router(health_router)
    app.include_router(providers_router)
    app.include_router(adapters_router)
    app.include_router(sessions_router)
    app.include_router(comments_router)
    app.include_router(memories_router)
    app.include_router(ws_plan_router)
    app.include_router(ws_act_router)
    return app


app = create_app()
