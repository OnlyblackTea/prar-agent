"""Comment API 集成测试 — 使用 TestClient + dependency override 注入 mock service。"""
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.comments import get_comment_service, router
from app.core.comment_schemas import CommentResponse
from app.services.comment_service import CommentNotFoundError, CommentService
from app.services.session_service import SessionNotFoundError


@pytest.fixture
def mock_service() -> CommentService:
    return MagicMock(spec=CommentService)


@pytest.fixture
def client(mock_service: CommentService) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def override_get_comment_service() -> CommentService:
        return mock_service

    app.dependency_overrides[get_comment_service] = override_get_comment_service
    return TestClient(app)


def make_comment_response(**overrides: Any) -> CommentResponse:
    defaults = {
        "id": uuid4(),
        "session_id": uuid4(),
        "plan_version": 1,
        "anchor_id": "abc123",
        "quote": "hello world",
        "quote_context": "ctx",
        "body": "my comment",
        "resolved": False,
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    c = MagicMock(spec=CommentResponse)
    for k, v in defaults.items():
        setattr(c, k, v)
    return c


class TestCreateComment:
    def test_create_ok(self, client: TestClient, mock_service: MagicMock) -> None:
        sid = str(uuid4())
        expected = make_comment_response()
        mock_service.create = AsyncMock(return_value=expected)

        resp = client.post(
            f"/api/sessions/{sid}/comments",
            json={
                "anchor_id": "abc123",
                "plan_version": 1,
                "quote": "hello world",
                "quote_context": "ctx",
                "body": "my comment",
            },
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["anchor_id"] == "abc123"
        assert data["body"] == "my comment"

    def test_create_session_not_found(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.create = AsyncMock(
            side_effect=SessionNotFoundError(uuid4()),
        )
        resp = client.post(
            f"/api/sessions/{uuid4()}/comments",
            json={
                "anchor_id": "a1",
                "plan_version": 1,
                "quote": "hi",
                "quote_context": "",
                "body": "test",
            },
        )
        assert resp.status_code == 404
        assert resp.json()["detail"] == "session_not_found"

    def test_create_phase_not_review(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.create = AsyncMock(
            side_effect=ValueError("phase_not_review"),
        )
        resp = client.post(
            f"/api/sessions/{uuid4()}/comments",
            json={
                "anchor_id": "a1",
                "plan_version": 1,
                "quote": "hi",
                "quote_context": "",
                "body": "test",
            },
        )
        assert resp.status_code == 409
        assert resp.json()["detail"] == "phase_not_review"

    def test_create_invalid_plan_version(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.create = AsyncMock(
            side_effect=ValueError("invalid_plan_version"),
        )
        resp = client.post(
            f"/api/sessions/{uuid4()}/comments",
            json={
                "anchor_id": "a1",
                "plan_version": 1,
                "quote": "hi",
                "quote_context": "",
                "body": "test",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "invalid_plan_version"


class TestListComments:
    def test_list_ok(self, client: TestClient, mock_service: MagicMock) -> None:
        c1 = make_comment_response()
        c2 = make_comment_response()
        mock_service.list_by_version = AsyncMock(return_value=[c1, c2])

        resp = client.get(f"/api/sessions/{uuid4()}/comments?plan_version=1")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2


class TestGetComment:
    def test_get_ok(self, client: TestClient, mock_service: MagicMock) -> None:
        sid = uuid4()
        expected = make_comment_response(session_id=sid)
        mock_service.get = AsyncMock(return_value=expected)

        resp = client.get(f"/api/sessions/{sid}/comments/{expected.id}")
        assert resp.status_code == 200

    def test_get_not_found(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.get = AsyncMock(
            side_effect=CommentNotFoundError("nope"),
        )
        resp = client.get(f"/api/sessions/{uuid4()}/comments/{uuid4()}")
        assert resp.status_code == 404

    def test_get_wrong_session(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        sid = uuid4()
        comment = make_comment_response(session_id=uuid4())  # different
        mock_service.get = AsyncMock(return_value=comment)

        resp = client.get(f"/api/sessions/{sid}/comments/{comment.id}")
        assert resp.status_code == 404
