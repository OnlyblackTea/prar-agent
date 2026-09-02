"""WebSocket: /api/ws/sessions/{session_id}/act — ACTING 执行 + 工具输出流式事件。

事件序列：step.start → (tool.stdout × N → tool.exit) × step → step.done × step → plan.done；
失败推 error 并关闭。错误码风格沿用 ws_plan。
"""

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, ValidationError

from app.api.ws_plan import get_router
from app.core.action_dispatcher import StepExecution, create_default_dispatcher
from app.core.logging import get_logger, request_id_var
from app.core.plan_schemas import PlanDocument, StepNode
from app.core.state_machine import Phase, transition
from app.db.session import get_sessionmaker
from app.services.adapter_service import AdapterNotFoundError, AdapterService
from app.services.session_service import SessionNotFoundError, SessionService

_log = get_logger("ws_act")

router = APIRouter(prefix="/api/ws", tags=["websocket"])


class ExecuteMessage(BaseModel):
    type: str = Field(pattern="^execute$")


class WSActSink:
    """ActEventSink 的 WS 实现：send_json 事件帧。放 api 层保持 core 零 fastapi 依赖。"""

    def __init__(self, websocket: WebSocket) -> None:
        self._ws = websocket

    async def step_start(self, *, index: int, step: StepNode) -> None:
        await self._ws.send_json(
            {
                "type": "step.start",
                "index": index,
                "step_id": step.id or f"step_{index:03d}",
                "title": step.title,
                "tool": step.tool,
                "tool_args": step.tool_args,
            }
        )

    async def tool_stdout(self, *, step_id: str, chunk: str) -> None:
        await self._ws.send_json(
            {"type": "tool.stdout", "step_id": step_id, "chunk": chunk}
        )

    async def tool_exit(self, *, step_id: str, exit_code: int, ok: bool) -> None:
        await self._ws.send_json(
            {"type": "tool.exit", "step_id": step_id, "exit_code": exit_code, "ok": ok}
        )

    async def step_done(self, *, record: StepExecution) -> None:
        await self._ws.send_json(
            {"type": "step.done", **record.model_dump(mode="json")}
        )


async def _send_error(ws: WebSocket, code: str, message: str) -> None:
    await ws.send_json({"type": "error", "code": code, "message": message})


async def _close_quietly(ws: WebSocket) -> None:
    try:
        await ws.close()
    except Exception:
        pass


@router.websocket("/sessions/{session_id}/act")
async def act_websocket(websocket: WebSocket, session_id: uuid.UUID) -> None:
    rid = uuid.uuid4().hex[:12]
    request_id_var.set(rid)

    await websocket.accept()
    _log.info("ws_connected", session_id=str(session_id))

    async with get_sessionmaker()() as db:
        try:
            raw = await websocket.receive_json()
            try:
                ExecuteMessage.model_validate(raw)
            except ValidationError as e:
                await _send_error(websocket, "invalid_message", str(e))
                return

            session_service = SessionService(db)
            try:
                session = await session_service.get(session_id)
            except SessionNotFoundError:
                await _send_error(websocket, "session_not_found", str(session_id))
                return

            if session.phase != Phase.ACTING.value:
                await _send_error(websocket, "illegal_phase", str(session.phase))
                return

            try:
                plan_row = await session_service.get_current_plan(session_id)
            except ValueError:
                await _send_error(websocket, "plan_not_found", str(session_id))
                return

            adapter_service = AdapterService(db)
            try:
                db_adapter = await adapter_service.get(session.adapter_id)
                adapter = adapter_service.resolve(db_adapter)
            except AdapterNotFoundError:
                await _send_error(
                    websocket, "adapter_not_found", str(session.adapter_id),
                )
                return

            plan = PlanDocument.model_validate(plan_row.document)
            dispatcher = create_default_dispatcher(get_router(), adapter)
            records = await dispatcher.execute_plan(
                plan,
                session_id=session_id,
                plan_version=plan_row.version,
                sink=WSActSink(websocket),
            )

            transition(
                Phase(session.phase), Phase.ACTION_REVIEW, session_id=str(session_id),
            )
            session.phase = Phase.ACTION_REVIEW.value
            await db.commit()

            await websocket.send_json(
                {
                    "type": "plan.done",
                    "total_steps": len(records),
                    "all_ok": all(r.ok for r in records),
                }
            )

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
