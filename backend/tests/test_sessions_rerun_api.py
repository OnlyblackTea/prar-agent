"""M4-26 POST /api/sessions/{id}/rerun 端点测试（TestClient + dependency_overrides）。"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.sessions import get_session_service, router
from app.core.state_machine import InvalidTransitionError, Phase
from app.services.session_service import SessionNotFoundError, SessionService


@pytest.fixture
def mock_service() -> MagicMock:
    return MagicMock(spec=SessionService)


@pytest.fixture
def client(mock_service: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def override_get_session_service() -> SessionService:
        return mock_service

    app.dependency_overrides[get_session_service] = override_get_session_service
    return TestClient(app)


def make_session_row(**overrides: Any) -> MagicMock:
    defaults: dict[str, Any] = {
        "id": uuid4(),
        "init_request": "r",
        "phase": "acting",
        "current_plan_version": 2,
        "adapter_id": uuid4(),
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


class TestRerunSession:
    def test_rerun_ok(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        row = make_session_row()
        mock_service.request_rerun = AsyncMock(return_value=row)

        resp = client.post(f"/api/sessions/{row.id}/rerun", json={"step_id": "step_002"})

        assert resp.status_code == 200
        assert resp.json() == {"phase": "acting", "rerun_from": "step_002"}
        mock_service.request_rerun.assert_awaited_once()
        kwargs = mock_service.request_rerun.await_args.kwargs
        assert kwargs == {"session_id": row.id, "step_id": "step_002"}

    def test_rerun_session_not_found_404(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        sid = uuid4()
        mock_service.request_rerun = AsyncMock(
            side_effect=SessionNotFoundError(sid),
        )
        resp = client.post(f"/api/sessions/{sid}/rerun", json={"step_id": "s"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "session_not_found"

    def test_rerun_illegal_phase_409(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.request_rerun = AsyncMock(
            side_effect=InvalidTransitionError(Phase.ACTING, Phase.ACTION_REVIEW),
        )
        resp = client.post(f"/api/sessions/{uuid4()}/rerun", json={"step_id": "s"})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "illegal_phase_transition"

    def test_rerun_no_run_409(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.request_rerun = AsyncMock(side_effect=ValueError("no_run"))
        resp = client.post(f"/api/sessions/{uuid4()}/rerun", json={"step_id": "s"})
        assert resp.status_code == 409
        assert resp.json()["detail"] == "no_run"

    def test_rerun_step_not_found_404(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.request_rerun = AsyncMock(
            side_effect=ValueError("step_not_found"),
        )
        resp = client.post(f"/api/sessions/{uuid4()}/rerun", json={"step_id": "s"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "step_not_found"

    def test_rerun_empty_step_id_404(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.request_rerun = AsyncMock(
            side_effect=ValueError("step_not_found"),
        )
        resp = client.post(f"/api/sessions/{uuid4()}/rerun", json={"step_id": ""})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "step_not_found"

    def test_rerun_step_not_executed_404(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.request_rerun = AsyncMock(
            side_effect=ValueError("step_not_executed"),
        )
        resp = client.post(f"/api/sessions/{uuid4()}/rerun", json={"step_id": "s"})
        assert resp.status_code == 404
        assert resp.json()["detail"] == "step_not_executed"
