"""WebSocket: /api/ws/sessions/{session_id}/plan"""

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger, request_id_var
from app.core.plan_engine import PlanEngine
from app.core.ws_streamer import stream_plan
from app.db.session import get_sessionmaker
from app.llm.router import LLMError, LLMRouter, LLMTransportError, StructuredOutputError
from app.llm.types import ResolvedAdapter
from app.services.adapter_service import AdapterNotFoundError, AdapterService
from app.services.session_service import SessionService

_log = get_logger("ws_plan")

router = APIRouter(prefix="/api/ws", tags=["websocket"])


class GenerateMessage(BaseModel):
    type: str = Field(pattern="^generate$")
    init_request: str = Field(min_length=1)
    adapter_id: uuid.UUID
    ltm_recall: list[str] = Field(default_factory=list)
    available_tools: list[str] | None = None


async def _resolve_dependencies(
    adapter_id: uuid.UUID,
    db: AsyncSession,
    router: LLMRouter,
) -> tuple[ResolvedAdapter, PlanEngine]:
    adapter_service = AdapterService(db)
    db_adapter = await adapter_service.get(adapter_id)
    resolved = adapter_service.resolve(db_adapter)
    plan_engine = PlanEngine(router)
    return resolved, plan_engine


async def _send_error(ws: WebSocket, code: str, message: str) -> None:
    await ws.send_json({"type": "error", "code": code, "message": message})


async def _close_quietly(ws: WebSocket) -> None:
    try:
        await ws.close()
    except Exception:
        pass


@router.websocket("/sessions/{session_id}/plan")
async def plan_websocket(websocket: WebSocket, session_id: uuid.UUID) -> None:
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)

    await websocket.accept()
    _log.info("ws_connected", session_id=str(session_id))

    async with get_sessionmaker()() as db:
        try:
            raw = await websocket.receive_json()
            try:
                msg = GenerateMessage.model_validate(raw)
            except ValidationError as e:
                await _send_error(websocket, "invalid_message", str(e))
                return

            try:
                adapter, plan_engine = await _resolve_dependencies(
                    msg.adapter_id, db, get_router(),
                )
            except AdapterNotFoundError:
                await _send_error(
                    websocket, "adapter_not_found", str(msg.adapter_id),
                )
                return

            try:
                plan = await plan_engine.generate(
                    init_request=msg.init_request,
                    adapter=adapter,
                    ltm_recall=msg.ltm_recall,
                    available_tools=msg.available_tools,
                )
            except LLMTransportError as e:
                await _send_error(websocket, "llm_transport", str(e))
                return
            except StructuredOutputError as e:
                await _send_error(websocket, "structured_output", str(e))
                return
            except LLMError as e:
                await _send_error(websocket, "llm_error", str(e))
                return

            # 持久化 plan 到 DB（stream 前）
            session_service = SessionService(db)
            await session_service.save_plan(session_id=session_id, plan=plan)
            await db.commit()

            async for event in stream_plan(plan, str(session_id)):
                await websocket.send_json(event)

        except WebSocketDisconnect:
            _log.info("ws_disconnected", session_id=str(session_id))
        except Exception as e:
            _log.exception("ws_internal_error", error=str(e))
            try:
                await _send_error(websocket, "internal", str(e))
            except Exception:
                pass
        finally:
            await _close_quietly(websocket)


def get_router() -> LLMRouter:
    """LLMRouter 单例，WS 和 HTTP 共享。"""
    from functools import lru_cache as _lru

    @_lru(maxsize=1)
    def _factory() -> LLMRouter:
        from app.config import get_settings

        return LLMRouter(get_settings())

    return _factory()

