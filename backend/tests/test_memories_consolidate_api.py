"""M4-24 POST /api/memories/consolidate 端点测试（TestClient + dependency_overrides）。"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.memories import get_consolidator, get_default_adapter, router
from app.core.embedding import EmbeddingTransportError
from app.llm.router import LLMTransportError
from app.llm.types import ResolvedAdapter


def make_adapter() -> ResolvedAdapter:
    return ResolvedAdapter(
        id=uuid4(),
        name="t",
        provider="openai",
        model="gpt-4o",
        credentials={"api_key": "sk-x"},
        params={},
    )


def make_result(**overrides: int) -> Any:
    from app.memory.consolidator import ConsolidateResult

    defaults = {
        "processed": 2,
        "distilled": 1,
        "inserted": 1,
        "merged": 0,
        "decayed": 3,
    }
    defaults.update(overrides)
    return ConsolidateResult(**defaults)


@pytest.fixture
def mock_consolidator() -> MagicMock:
    return MagicMock()


@pytest.fixture
def client(mock_consolidator: MagicMock) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    async def override_get_consolidator() -> Any:
        return mock_consolidator

    async def override_get_default_adapter() -> ResolvedAdapter:
        return make_adapter()

    app.dependency_overrides[get_consolidator] = override_get_consolidator
    app.dependency_overrides[get_default_adapter] = override_get_default_adapter
    return TestClient(app)


class TestConsolidateMemories:
    def test_consolidate_ok_passes_adapter(
        self, client: TestClient, mock_consolidator: MagicMock,
    ) -> None:
        mock_consolidator.run_once = AsyncMock(return_value=make_result())

        resp = client.post("/api/memories/consolidate")

        assert resp.status_code == 200
        assert resp.json() == {
            "processed": 2,
            "distilled": 1,
            "inserted": 1,
            "merged": 0,
            "decayed": 3,
        }
        mock_consolidator.run_once.assert_awaited_once()
        adapter = mock_consolidator.run_once.await_args.kwargs["adapter"]
        assert adapter is not None
        assert adapter.provider == "openai"

    def test_consolidate_no_default_adapter_503(
        self, client: TestClient, mock_consolidator: MagicMock,
    ) -> None:
        from app.memory.consolidator import NoDefaultAdapterError

        mock_consolidator.run_once = AsyncMock(
            side_effect=NoDefaultAdapterError("no default adapter"),
        )
        resp = client.post("/api/memories/consolidate")
        assert resp.status_code == 503
        assert resp.json()["detail"] == "no_default_adapter"

    def test_consolidate_llm_error_502(
        self, client: TestClient, mock_consolidator: MagicMock,
    ) -> None:
        mock_consolidator.run_once = AsyncMock(
            side_effect=LLMTransportError("boom", model_id="m"),
        )
        resp = client.post("/api/memories/consolidate")
        assert resp.status_code == 502
        assert resp.json()["detail"] == "llm_failed"

    def test_consolidate_embedding_error_502(
        self, client: TestClient, mock_consolidator: MagicMock,
    ) -> None:
        mock_consolidator.run_once = AsyncMock(
            side_effect=EmbeddingTransportError("boom", model_id="m"),
        )
        resp = client.post("/api/memories/consolidate")
        assert resp.status_code == 502
        assert resp.json()["detail"] == "embedding_failed"
