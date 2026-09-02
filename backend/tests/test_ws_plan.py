"""WebSocket endpoint 集成测试（mock PlanEngine + adapter resolve）。"""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.plan_schemas import PlanDocument, StepNode
from app.llm.router import LLMTransportError, StructuredOutputError
from app.main import app


@pytest.fixture
def ws_client() -> TestClient:
    return TestClient(app)


def _valid_msg(adapter_id: str | None = None) -> dict[str, str]:
    return {
        "type": "generate",
        "init_request": "build a web app",
        "adapter_id": adapter_id or str(uuid.uuid4()),
    }


def _mock_plan() -> PlanDocument:
    return PlanDocument(
        title="Plan",
        summary="Summary",
        nodes=[
            StepNode(title="s1", description="d1", tool="shell"),
            StepNode(title="s2", description="d2", tool="fs.read"),
        ],
    )


# ===== T1: 非 generate 消息 → invalid_message =====


def test_ws_rejects_invalid_first_message(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/plan") as ws:
        ws.send_json({"type": "cancel"})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert resp["code"] == "invalid_message"


# ===== T2: 缺 init_request → invalid_message =====


def test_ws_rejects_missing_required_field(ws_client: TestClient) -> None:
    sid = uuid.uuid4()
    with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/plan") as ws:
        ws.send_json({"type": "generate", "adapter_id": str(uuid.uuid4())})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert resp["code"] == "invalid_message"


# ===== T3: adapter_not_found → error =====


def test_ws_returns_adapter_not_found(ws_client: TestClient) -> None:
    from app.services.adapter_service import AdapterNotFoundError

    with patch(
        "app.api.ws_plan._resolve_dependencies",
        new_callable=AsyncMock,
        side_effect=AdapterNotFoundError(uuid.uuid4()),
    ):
        sid = uuid.uuid4()
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/plan") as ws:
            ws.send_json(_valid_msg())
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["code"] == "adapter_not_found"


# ===== T4: LLMTransportError → error =====


def test_ws_returns_llm_transport_error(ws_client: TestClient) -> None:
    mock_adapter = MagicMock()
    mock_engine = AsyncMock()
    mock_engine.generate.side_effect = LLMTransportError(
        "connection failed", model_id="gpt-4"
    )

    with (
        patch(
            "app.api.ws_plan._resolve_dependencies",
            new_callable=AsyncMock,
            return_value=(mock_adapter, mock_engine),
        ),
        patch(
            "app.api.ws_plan._recall_ltm",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        sid = uuid.uuid4()
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/plan") as ws:
            ws.send_json(_valid_msg())
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["code"] == "llm_transport"


# ===== T5: StructuredOutputError → error =====


def test_ws_returns_structured_output_error(ws_client: TestClient) -> None:
    mock_adapter = MagicMock()
    mock_engine = AsyncMock()
    mock_engine.generate.side_effect = StructuredOutputError(
        "bad schema", model_id="gpt-4"
    )

    with (
        patch(
            "app.api.ws_plan._resolve_dependencies",
            new_callable=AsyncMock,
            return_value=(mock_adapter, mock_engine),
        ),
        patch(
            "app.api.ws_plan._recall_ltm",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        sid = uuid.uuid4()
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/plan") as ws:
            ws.send_json(_valid_msg())
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["code"] == "structured_output"


# ===== T6: 成功路径完整事件序列 =====


def test_ws_success_emits_full_event_sequence(ws_client: TestClient) -> None:
    plan = _mock_plan()
    mock_adapter = MagicMock()
    mock_engine = AsyncMock()
    mock_engine.generate.return_value = plan
    mock_session_svc = AsyncMock()

    with (
        patch(
            "app.api.ws_plan._resolve_dependencies",
            new_callable=AsyncMock,
            return_value=(mock_adapter, mock_engine),
        ),
        patch(
            "app.api.ws_plan.SessionService",
            return_value=mock_session_svc,
        ),
        patch(
            "app.api.ws_plan._recall_ltm",
            new_callable=AsyncMock,
            return_value=[],
        ),
    ):
        sid = uuid.uuid4()
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/plan") as ws:
            ws.send_json(_valid_msg())

            events = []
            while True:
                try:
                    events.append(ws.receive_json())
                except Exception:
                    break

            assert events[0]["type"] == "plan.start"
            assert events[0]["title"] == "Plan"
            assert events[0]["session_id"] == str(sid)

            node_events = [e for e in events if e["type"] == "plan.node"]
            assert len(node_events) == 2
            assert node_events[0]["index"] == 0
            assert node_events[1]["index"] == 1

            assert events[-1]["type"] == "plan.done"
            assert events[-1]["total_nodes"] == 2


# ===== T7: recall 命中注入 generate 的 ltm_recall =====


def test_ws_injects_recalled_memories_into_generate(
    ws_client: TestClient,
) -> None:
    plan = _mock_plan()
    mock_adapter = MagicMock()
    mock_engine = AsyncMock()
    mock_engine.generate.return_value = plan
    mock_session_svc = AsyncMock()
    lines = ["[semantic|0.82|0.90] 用户偏好 React"]

    with (
        patch(
            "app.api.ws_plan._resolve_dependencies",
            new_callable=AsyncMock,
            return_value=(mock_adapter, mock_engine),
        ),
        patch(
            "app.api.ws_plan.SessionService",
            return_value=mock_session_svc,
        ),
        patch(
            "app.api.ws_plan._recall_ltm",
            new_callable=AsyncMock,
            return_value=lines,
        ) as mock_recall,
    ):
        sid = uuid.uuid4()
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/plan") as ws:
            ws.send_json(_valid_msg())
            while True:
                try:
                    ws.receive_json()
                except Exception:
                    break

    mock_recall.assert_awaited_once()
    assert mock_recall.await_args.kwargs["query"] == "build a web app"
    assert mock_engine.generate.await_count == 1
    assert mock_engine.generate.await_args.kwargs["ltm_recall"] == lines


# ===== T8: recall embedding 失败 → embedding_failed =====


def test_ws_returns_embedding_failed(ws_client: TestClient) -> None:
    from app.core.embedding import EmbeddingTransportError

    mock_adapter = MagicMock()
    mock_engine = AsyncMock()

    with (
        patch(
            "app.api.ws_plan._resolve_dependencies",
            new_callable=AsyncMock,
            return_value=(mock_adapter, mock_engine),
        ),
        patch(
            "app.api.ws_plan._recall_ltm",
            new_callable=AsyncMock,
            side_effect=EmbeddingTransportError(
                "embedding down", model_id="text-embedding-v1",
            ),
        ),
    ):
        sid = uuid.uuid4()
        with ws_client.websocket_connect(f"/api/ws/sessions/{sid}/plan") as ws:
            ws.send_json(_valid_msg())
            resp = ws.receive_json()
            assert resp["type"] == "error"
            assert resp["code"] == "embedding_failed"

    mock_engine.generate.assert_not_awaited()
