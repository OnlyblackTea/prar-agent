"""Memory API 集成测试 — TestClient + dependency_overrides 注入 mock MemoryService。"""
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memories import get_memory_service, router
from app.core.embedding import EmbeddingDimensionError, EmbeddingTransportError
from app.db.models import Memory
from app.services.memory_service import MemoryHit, MemoryService


@pytest.fixture
def mock_service() -> MagicMock:
    return MagicMock(spec=MemoryService)


@pytest.fixture
def client(mock_service: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def override_get_memory_service() -> MemoryService:
        return mock_service

    app.dependency_overrides[get_memory_service] = override_get_memory_service
    return TestClient(app)


def make_memory_row(**overrides: Any) -> MagicMock:
    defaults: dict[str, Any] = {
        "id": uuid4(),
        "kind": "episodic",
        "content": "hello",
        "importance": 0.5,
        "user_id": None,
        "source_session": None,
        "last_accessed": datetime.now(UTC),
        "access_count": 0,
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    mem = MagicMock(spec=Memory)
    for k, v in defaults.items():
        setattr(mem, k, v)
    return mem


class TestCreateMemory:
    def test_create_ok_and_no_embedding_leak(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        row = make_memory_row(content="设计决策已确认", importance=0.8)
        mock_service.store = AsyncMock(return_value=row)

        resp = client.post(
            "/api/memories",
            json={"kind": "episodic", "content": "设计决策已确认", "importance": 0.8},
        )

        assert resp.status_code == 201
        data = resp.json()
        assert data["id"] == str(row.id)
        assert data["kind"] == "episodic"
        assert data["content"] == "设计决策已确认"
        assert data["importance"] == 0.8
        assert "embedding" not in data
        mock_service.store.assert_awaited_once()

    def test_create_transport_error_502(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.store = AsyncMock(
            side_effect=EmbeddingTransportError("boom", model_id="m"),
        )
        resp = client.post(
            "/api/memories",
            json={"kind": "episodic", "content": "x"},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "embedding_failed"

    def test_create_dimension_error_502(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.store = AsyncMock(
            side_effect=EmbeddingDimensionError(
                "dim mismatch", expected=1536, actual=1024, model_id="m",
            ),
        )
        resp = client.post(
            "/api/memories",
            json={"kind": "episodic", "content": "x"},
        )
        assert resp.status_code == 502
        assert resp.json()["detail"] == "embedding_dimension_mismatch"

    def test_create_service_value_error_400(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.store = AsyncMock(side_effect=ValueError("kind"))
        resp = client.post(
            "/api/memories",
            json={"kind": "episodic", "content": "x"},
        )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "kind"

    def test_create_invalid_kind_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/memories",
            json={"kind": "hacker", "content": "x"},
        )
        assert resp.status_code == 422

    def test_create_blank_content_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/memories",
            json={"kind": "episodic", "content": ""},
        )
        assert resp.status_code == 422

    def test_create_importance_out_of_range_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/memories",
            json={"kind": "episodic", "content": "x", "importance": 1.5},
        )
        assert resp.status_code == 422


class TestSearchMemories:
    def test_search_ok_and_no_embedding_leak(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        row = make_memory_row(content="hello")
        mock_service.search = AsyncMock(
            return_value=[MemoryHit(memory=row, score=0.95)],
        )

        resp = client.post("/api/memories/search", json={"query": "hello"})

        assert resp.status_code == 200
        data = resp.json()
        assert len(data["hits"]) == 1
        hit = data["hits"][0]
        assert hit["id"] == str(row.id)
        assert hit["content"] == "hello"
        assert hit["score"] == 0.95
        assert "embedding" not in hit

    def test_search_passes_kinds_and_limit(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.search = AsyncMock(return_value=[])

        resp = client.post(
            "/api/memories/search",
            json={"query": "x", "kinds": ["procedural"], "limit": 10},
        )

        assert resp.status_code == 200
        kwargs = mock_service.search.await_args.kwargs
        assert kwargs["kinds"] == ["procedural"]
        assert kwargs["limit"] == 10

    def test_search_transport_error_502(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.search = AsyncMock(
            side_effect=EmbeddingTransportError("boom", model_id="m"),
        )
        resp = client.post("/api/memories/search", json={"query": "x"})
        assert resp.status_code == 502
        assert resp.json()["detail"] == "embedding_failed"

    def test_search_dimension_error_502(
        self, client: TestClient, mock_service: MagicMock,
    ) -> None:
        mock_service.search = AsyncMock(
            side_effect=EmbeddingDimensionError(
                "dim mismatch", expected=1536, actual=1024, model_id="m",
            ),
        )
        resp = client.post("/api/memories/search", json={"query": "x"})
        assert resp.status_code == 502
        assert resp.json()["detail"] == "embedding_dimension_mismatch"

    def test_search_blank_query_422(self, client: TestClient) -> None:
        resp = client.post("/api/memories/search", json={"query": ""})
        assert resp.status_code == 422

    def test_search_limit_zero_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/memories/search", json={"query": "x", "limit": 0},
        )
        assert resp.status_code == 422

    def test_search_invalid_kind_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/memories/search", json={"query": "x", "kinds": ["bad"]},
        )
        assert resp.status_code == 422
