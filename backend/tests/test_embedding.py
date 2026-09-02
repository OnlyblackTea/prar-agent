"""EmbeddingService 单元测试 — 全部 mock AsyncOpenAI 客户端，不真调 API。"""
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from openai import OpenAIError

from app.config import Settings
from app.core.embedding import (
    EmbeddingDimensionError,
    EmbeddingService,
    EmbeddingTransportError,
    get_embedding_service,
)

DIM = 4


def make_settings(**overrides: Any) -> Settings:
    defaults: dict[str, Any] = {
        "embedding_model": "test-embed",
        "embedding_dim": DIM,
        "embedding_base_url": None,
        "embedding_api_key": None,
    }
    defaults.update(overrides)
    return Settings(**defaults)


def make_response(vectors: list[list[float]]) -> MagicMock:
    resp = MagicMock()
    resp.data = [MagicMock(embedding=v) for v in vectors]
    resp.usage = MagicMock(total_tokens=len(vectors))
    return resp


def make_client(return_value: MagicMock | None = None, **overrides: Any) -> MagicMock:
    client = MagicMock()
    client.embeddings = MagicMock()
    if "side_effect" in overrides:
        client.embeddings.create = AsyncMock(side_effect=overrides["side_effect"])
    else:
        client.embeddings.create = AsyncMock(
            return_value=return_value if return_value is not None else make_response([])
        )
    return client


class TestEmbed:
    async def test_returns_vectors_in_input_order(self) -> None:
        v1 = [1.0] * DIM
        v2 = [2.0] * DIM
        client = make_client(make_response([v1, v2]))
        svc = EmbeddingService(make_settings(), client=client)

        vecs = await svc.embed(["hello", "world"])

        assert vecs == [v1, v2]
        kwargs = client.embeddings.create.await_args.kwargs
        assert kwargs["model"] == "test-embed"
        assert kwargs["input"] == ["hello", "world"]

    async def test_embed_one_returns_single_vector(self) -> None:
        v = [0.5] * DIM
        client = make_client(make_response([v]))
        svc = EmbeddingService(make_settings(), client=client)

        vec = await svc.embed_one("solo")

        assert vec == v
        kwargs = client.embeddings.create.await_args.kwargs
        assert kwargs["input"] == ["solo"]

    async def test_dimension_mismatch_raises(self) -> None:
        bad = [1.0] * (DIM - 1)
        client = make_client(make_response([bad]))
        svc = EmbeddingService(make_settings(), client=client)

        with pytest.raises(EmbeddingDimensionError) as ei:
            await svc.embed(["x"])
        assert ei.value.expected == DIM
        assert ei.value.actual == DIM - 1
        assert ei.value.model_id == "test-embed"

    async def test_sdk_error_wrapped_as_transport(self) -> None:
        client = make_client(side_effect=OpenAIError("boom"))
        svc = EmbeddingService(make_settings(), client=client)

        with pytest.raises(EmbeddingTransportError) as ei:
            await svc.embed(["x"])
        assert ei.value.model_id == "test-embed"
        assert isinstance(ei.value.cause, OpenAIError)

    async def test_empty_input_raises_without_api_call(self) -> None:
        client = make_client()
        svc = EmbeddingService(make_settings(), client=client)

        with pytest.raises(ValueError):
            await svc.embed([])
        client.embeddings.create.assert_not_called()

    async def test_blank_text_raises_without_api_call(self) -> None:
        client = make_client()
        svc = EmbeddingService(make_settings(), client=client)

        with pytest.raises(ValueError):
            await svc.embed(["   "])
        client.embeddings.create.assert_not_called()


class TestClientConstruction:
    def test_override_base_url_and_api_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class FakeClient:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        import app.core.embedding as mod

        monkeypatch.setattr(mod, "AsyncOpenAI", FakeClient)
        svc = EmbeddingService(
            make_settings(
                embedding_base_url="http://127.0.0.1:8080/v1",
                embedding_api_key="local-key",
            )
        )

        client = svc._get_client()

        assert isinstance(client, FakeClient)
        assert captured == {
            "api_key": "local-key",
            "base_url": "http://127.0.0.1:8080/v1",
        }

    def test_defaults_leave_kwargs_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class FakeClient:
            def __init__(self, **kwargs: Any) -> None:
                captured.update(kwargs)

        import app.core.embedding as mod

        monkeypatch.setattr(mod, "AsyncOpenAI", FakeClient)
        svc = EmbeddingService(make_settings())

        svc._get_client()

        assert captured == {}


class TestSingleton:
    def test_get_embedding_service_is_cached(self) -> None:
        assert get_embedding_service() is get_embedding_service()
