"""M4-23 POST /api/sessions/{id}/complete 端点测试（TestClient + dependency_overrides）。"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.sessions import get_long_term, get_session_service, router
from app.core.embedding import EmbeddingDimensionError, EmbeddingTransportError
from app.core.state_machine import InvalidTransitionError, Phase
from app.services.session_service import SessionNotFoundError, SessionService


class _FakeLongTerm:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def record_episodic(self, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(kwargs)


@pytest.fixture
def mock_service() -> MagicMock:
    return MagicMock(spec=SessionService)


@pytest.fixture
def fake_ltm() -> _FakeLongTerm:
    return _FakeLongTerm()


@pytest.fixture
def client(mock_service: MagicMock, fake_ltm: _FakeLongTerm) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def override_get_session_service() -> SessionService:
        return mock_service

    async def override_get_long_term() -> Any:
        return fake_ltm

    app.dependency_overrides[get_session_service] = override_get_session_service
    app.dependency_overrides[get_long_term] = override_get_long_term
    return TestClient(app)


def make_session_row(**overrides: Any) -> MagicMock:
    defaults: dict[str, Any] = {
        "id": uuid4(),
        "init_request": "r",
        "phase": "done",
        "current_plan_version": 2,
        "adapter_id": uuid4(),
    }
    defaults.update(overrides)
    s = MagicMock()
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


class TestCompleteSession:
    def test_complete_ok(
        self, client: TestClient, mock_service: MagicMock, fake_ltm: _FakeLongTerm,
    ) -> None:
        row = make_session_row()
        mock_service.complete = AsyncMock(return_value=row)

        resp = client.post(f"/api/sessions/{row.id}/complete")

        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == str(row.id)
        assert data["phase"] == "done"
        assert data["current_plan_version"] == 2
        mock_service.complete.assert_awaited_once()
        kwargs = mock_service.complete.await_args.kwargs
        assert kwargs["session_id"] == row.id
        assert kwargs["long_term"] is fake_ltm

    def test_complete_illegal_phase_409(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.complete = AsyncMock(
            side_effect=InvalidTransitionError(Phase.ACTING, Phase.DONE),
        )
        resp = client.post(f"/api/sessions/{uuid4()}/complete")
        assert resp.status_code == 409
        assert resp.json()["detail"] == "illegal_phase_transition"

    def test_complete_session_not_found_404(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        sid = uuid4()
        mock_service.complete = AsyncMock(side_effect=SessionNotFoundError(sid))
        resp = client.post(f"/api/sessions/{sid}/complete")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "session_not_found"

    def test_complete_plan_not_found_404(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.complete = AsyncMock(side_effect=ValueError("plan_not_found"))
        resp = client.post(f"/api/sessions/{uuid4()}/complete")
        assert resp.status_code == 404
        assert resp.json()["detail"] == "plan_not_found"

    def test_complete_embedding_transport_502(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.complete = AsyncMock(
            side_effect=EmbeddingTransportError("boom", model_id="m"),
        )
        resp = client.post(f"/api/sessions/{uuid4()}/complete")
        assert resp.status_code == 502
        assert resp.json()["detail"] == "embedding_failed"

    def test_complete_embedding_dimension_502(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.complete = AsyncMock(
            side_effect=EmbeddingDimensionError(
                "dim", expected=1536, actual=1024, model_id="m",
            ),
        )
        resp = client.post(f"/api/sessions/{uuid4()}/complete")
        assert resp.status_code == 502
        assert resp.json()["detail"] == "embedding_dimension_mismatch"
