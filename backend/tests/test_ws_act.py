"""Task 19 /api/ws/sessions/{id}/act 端点集成测试（patch service + dispatcher 工厂）。"""

import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.action_dispatcher import StepExecution
from app.core.plan_schemas import PlanDocument, StepNode
from app.main import app
from app.services.session_service import SessionNotFoundError
from app.tools.base import ToolExecutionError


@pytest.fixture
def ws_client() -> TestClient:
    return TestClient(app)


def _execute_msg() -> dict[str, str]:
    return {"type": "execute"}


def _acting_session(sid: uuid.UUID) -> MagicMock:
    s = MagicMock()
    s.phase = "acting"
    s.id = sid
    s.adapter_id = uuid.uuid4()
    s.metadata_json = {}
    return s


def _mock_plan_row(version: int = 2) -> MagicMock:
    row = MagicMock()
    row.version = version
    row.document = PlanDocument(
        title="t",
        summary="",
        nodes=[StepNode(title="s", description="d", tool="shell")],
    ).model_dump(mode="json")
    return row


class _ScriptedDispatcher:
    """create_default_dispatcher 替身：按脚本推 sink 事件并返回 records。"""

    async def execute_plan(
        self,
        plan: PlanDocument,
        *,
        session_id: uuid.UUID,
        plan_version: int,
        sink: Any = None,
        start_from: str | None = None,
    ) -> list[StepExecution]:
        assert sink is not None
        step = plan.nodes[0]
        assert isinstance(step, StepNode)
        await sink.step_start(index=0, step=step)
        await sink.tool_stdout(step_id="step_000", chunk="hello\n")
        await sink.tool_exit(step_id="step_000", exit_code=0, ok=True)
        record = StepExecution(step_id="step_000", ok=True, attempts=1, output="exit_code=0")
        await sink.step_done(record=record)
        return [record]


# ===== W1: 非 execute 消息 → invalid_message =====


def test_ws_act_rejects_invalid_message(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/act") as ws:
        ws.send_json({"type": "cancel"})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert resp["code"] == "invalid_message"


# ===== W2: session 不存在 → session_not_found =====


def test_ws_act_session_not_found(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    mock_svc = AsyncMock()
    mock_svc.get.side_effect = SessionNotFoundError(sid)
    with patch("app.api.ws_act.SessionService", return_value=mock_svc):
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/act") as ws:
            ws.send_json(_execute_msg())
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["code"] == "session_not_found"


# ===== W3: phase 非 acting → illegal_phase =====


def test_ws_act_illegal_phase(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    mock_svc = AsyncMock()
    mock_svc.get.return_value = MagicMock(phase="plan_review")
    with patch("app.api.ws_act.SessionService", return_value=mock_svc):
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/act") as ws:
            ws.send_json(_execute_msg())
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["code"] == "illegal_phase"


# ===== W4: 完整事件序列 + phase 转移 =====


def test_ws_act_success_full_sequence_and_transition(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    session = _acting_session(sid)
    mock_svc = AsyncMock()
    mock_svc.get.return_value = session
    mock_svc.get_current_plan.return_value = _mock_plan_row(version=3)
    mock_adapter_svc = MagicMock()
    mock_adapter_svc.get = AsyncMock(return_value=MagicMock())
    mock_adapter_svc.resolve = MagicMock(return_value=MagicMock())

    with (
        patch("app.api.ws_act.SessionService", return_value=mock_svc),
        patch("app.api.ws_act.AdapterService", return_value=mock_adapter_svc),
        patch("app.api.ws_act.create_default_dispatcher", return_value=_ScriptedDispatcher()),
        patch("app.api.ws_act.get_router", return_value=MagicMock()),
    ):
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/act") as ws:
            ws.send_json(_execute_msg())
            events: list[dict[str, Any]] = []
            while True:
                try:
                    events.append(ws.receive_json())
                except Exception:
                    break

            assert [e["type"] for e in events] == [
                "step.start", "tool.stdout", "tool.exit", "step.done", "plan.done",
            ]
            assert events[0]["index"] == 0
            assert events[0]["step_id"] == "step_000"
            assert events[0]["title"] == "s"
            assert events[0]["tool"] == "shell"
            assert events[0]["tool_args"] == {}
            assert events[1] == {"type": "tool.stdout", "step_id": "step_000", "chunk": "hello\n"}
            assert events[2] == {
                "type": "tool.exit", "step_id": "step_000", "exit_code": 0, "ok": True,
            }
            assert events[3]["type"] == "step.done"
            assert events[3]["ok"] is True
            assert events[3]["output"] == "exit_code=0"
            assert events[4] == {"type": "plan.done", "total_steps": 1, "all_ok": True}
            assert session.phase == "action_review"
            assert session.metadata_json == {
                "last_run": {
                    "plan_version": 3,
                    "all_ok": True,
                    "steps": [
                        {"step_id": "step_000", "ok": True, "git_commit": None}
                    ],
                }
            }


# ===== W5: plan 不存在 → plan_not_found =====


def test_ws_act_plan_not_found(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    mock_svc = AsyncMock()
    mock_svc.get.return_value = _acting_session(sid)
    mock_svc.get_current_plan.side_effect = ValueError("No plan for session")
    with patch("app.api.ws_act.SessionService", return_value=mock_svc):
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/act") as ws:
            ws.send_json(_execute_msg())
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["code"] == "plan_not_found"


# ===== W6: 执行中环境故障 → internal =====


def test_ws_act_tool_execution_error_internal(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    mock_svc = AsyncMock()
    mock_svc.get.return_value = _acting_session(sid)
    mock_svc.get_current_plan.return_value = _mock_plan_row()
    mock_adapter_svc = MagicMock()
    mock_adapter_svc.get = AsyncMock(return_value=MagicMock())
    mock_adapter_svc.resolve = MagicMock(return_value=MagicMock())
    dispatcher = AsyncMock()
    dispatcher.execute_plan.side_effect = ToolExecutionError("boom")

    with (
        patch("app.api.ws_act.SessionService", return_value=mock_svc),
        patch("app.api.ws_act.AdapterService", return_value=mock_adapter_svc),
        patch("app.api.ws_act.create_default_dispatcher", return_value=dispatcher),
        patch("app.api.ws_act.get_router", return_value=MagicMock()),
    ):
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/act") as ws:
            ws.send_json(_execute_msg())
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["code"] == "internal"


# ===== W10-W12: pending_rerun_from 消费（Task 26 局部 rerun） =====


def _ok_record() -> StepExecution:
    return StepExecution(
        step_id="step_000", ok=True, attempts=1, output="exit_code=0",
        git_commit="a" * 40,
    )


def test_ws_act_consumes_pending_rerun(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    session = _acting_session(sid)
    session.metadata_json = {"pending_rerun_from": "step_000"}
    mock_svc = AsyncMock()
    mock_svc.get.return_value = session
    mock_svc.get_current_plan.return_value = _mock_plan_row(version=3)
    mock_adapter_svc = MagicMock()
    mock_adapter_svc.get = AsyncMock(return_value=MagicMock())
    mock_adapter_svc.resolve = MagicMock(return_value=MagicMock())
    dispatcher = AsyncMock()
    dispatcher.execute_plan.return_value = [_ok_record()]

    with (
        patch("app.api.ws_act.SessionService", return_value=mock_svc),
        patch("app.api.ws_act.AdapterService", return_value=mock_adapter_svc),
        patch("app.api.ws_act.create_default_dispatcher", return_value=dispatcher),
        patch("app.api.ws_act.get_router", return_value=MagicMock()),
    ):
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/act") as ws:
            ws.send_json(_execute_msg())
            resp = ws.receive_json()
            assert resp["type"] == "plan.done"
            assert resp["all_ok"] is True

    kwargs = dispatcher.execute_plan.await_args.kwargs
    assert kwargs["start_from"] == "step_000"
    assert kwargs["plan_version"] == 3
    assert session.phase == "action_review"
    # last_run 覆盖 + pending 删除
    assert session.metadata_json == {
        "last_run": {
            "plan_version": 3,
            "all_ok": True,
            "steps": [{"step_id": "step_000", "ok": True, "git_commit": "a" * 40}],
        }
    }


def test_ws_act_no_pending_full_path(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    session = _acting_session(sid)
    mock_svc = AsyncMock()
    mock_svc.get.return_value = session
    mock_svc.get_current_plan.return_value = _mock_plan_row(version=2)
    mock_adapter_svc = MagicMock()
    mock_adapter_svc.get = AsyncMock(return_value=MagicMock())
    mock_adapter_svc.resolve = MagicMock(return_value=MagicMock())
    dispatcher = AsyncMock()
    dispatcher.execute_plan.return_value = [_ok_record()]

    with (
        patch("app.api.ws_act.SessionService", return_value=mock_svc),
        patch("app.api.ws_act.AdapterService", return_value=mock_adapter_svc),
        patch("app.api.ws_act.create_default_dispatcher", return_value=dispatcher),
        patch("app.api.ws_act.get_router", return_value=MagicMock()),
    ):
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/act") as ws:
            ws.send_json(_execute_msg())
            resp = ws.receive_json()
            assert resp["type"] == "plan.done"

    assert dispatcher.execute_plan.await_args.kwargs["start_from"] is None
    assert session.metadata_json == {
        "last_run": {
            "plan_version": 2,
            "all_ok": True,
            "steps": [{"step_id": "step_000", "ok": True, "git_commit": "a" * 40}],
        }
    }


def test_ws_act_pending_failure_rolls_back_phase(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    session = _acting_session(sid)
    session.metadata_json = {"pending_rerun_from": "step_000"}
    mock_svc = AsyncMock()
    mock_svc.get.return_value = session
    mock_svc.get_current_plan.return_value = _mock_plan_row(version=2)
    mock_adapter_svc = MagicMock()
    mock_adapter_svc.get = AsyncMock(return_value=MagicMock())
    mock_adapter_svc.resolve = MagicMock(return_value=MagicMock())
    dispatcher = AsyncMock()
    dispatcher.execute_plan.side_effect = ToolExecutionError("boom")

    with (
        patch("app.api.ws_act.SessionService", return_value=mock_svc),
        patch("app.api.ws_act.AdapterService", return_value=mock_adapter_svc),
        patch("app.api.ws_act.create_default_dispatcher", return_value=dispatcher),
        patch("app.api.ws_act.get_router", return_value=MagicMock()),
    ):
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/act") as ws:
            ws.send_json(_execute_msg())
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["code"] == "internal"

    # D5：phase 回 action_review，pending 保留（重试安全）
    assert session.phase == "action_review"
    assert session.metadata_json.get("pending_rerun_from") == "step_000"
