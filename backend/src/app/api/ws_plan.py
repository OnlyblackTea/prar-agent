"""WebSocket: /api/ws/sessions/{session_id}/plan"""

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from app.core.logging import get_logger, request_id_var
from app.core.ws_streamer import stream_plan
from app.llm.router import LLMError, LLMTransportError, StructuredOutputError
from app.services.adapter_service import AdapterNotFoundError

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
) -> tuple:
    """解析 adapter + 构造 PlanEngine。TODO: Task 10+ 接 DB session middleware。"""
    raise NotImplementedError(
        f"adapter resolve not yet implemented (adapter_id={adapter_id}). "
        "Requires DB session middleware — see Task 10+."
    )


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

    try:
        raw = await websocket.receive_json()
        try:
            msg = GenerateMessage.model_validate(raw)
        except ValidationError as e:
            await _send_error(websocket, "invalid_message", str(e))
            return

        try:
            adapter, plan_engine = await _resolve_dependencies(msg.adapter_id)
        except AdapterNotFoundError:
            await _send_error(
                websocket, "adapter_not_found", str(msg.adapter_id)
            )
            return
        except NotImplementedError as e:
            await _send_error(websocket, "internal", str(e))
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
            await _send_error(websocket, "llm_transport", str(e))
            return

        async for event in stream_plan(plan, str(session_id)):
            await websocket.send_json(event)

    except WebSocketDisconnect:
        _log.info("ws_disconnected", session_id=str(session_id))
    except Exception as e:
        _log.exception("ws_internal_error", error=str(e))
        await _send_error(websocket, "internal", str(e))
    finally:
        await _close_quietly(websocket)
