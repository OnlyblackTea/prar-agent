"""M4-24 Consolidator 单元测试 — 全 mock（db/store/router/embedding 均替身）。"""

import asyncio
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.embedding import EmbeddingError
from app.llm.router import LLMRouter, LLMTransportError
from app.services.memory_service import MemoryService

VEC = [0.1] * 1536


def make_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.refresh = AsyncMock()
    return db


def make_store() -> MagicMock:
    return MagicMock(spec=MemoryService)


def make_router() -> MagicMock:
    return MagicMock(spec=LLMRouter)


def make_embedding() -> MagicMock:
    from app.core.embedding import EmbeddingService

    return MagicMock(spec=EmbeddingService)


def make_adapter() -> Any:
    from uuid import uuid4

    from app.llm.types import ResolvedAdapter

    return ResolvedAdapter(
        id=uuid4(),
        name="t",
        provider="openai",
        model="gpt-4o",
        credentials={"api_key": "sk-x"},
        params={},
    )


def make_memory_row(content: str = "e1") -> MagicMock:
    from uuid import uuid4

    from app.db.models import Memory

    mem = MagicMock(spec=Memory)
    mem.id = uuid4()
    mem.kind = "episodic"
    mem.content = content
    mem.consolidated_at = None
    return mem


def make_structured_response(items: list[Any]) -> Any:
    from app.llm.router import StructuredResponse, TokenUsage
    from app.memory.consolidator import ConsolidatedMemories

    parsed = ConsolidatedMemories(items=items)
    return StructuredResponse[ConsolidatedMemories](
        parsed=parsed,
        raw_text=parsed.model_dump_json(),
        model_id="gpt-4o",
        usage=TokenUsage(),
        finish_reason="stop",
    )


def make_item(
    kind: Literal["semantic", "procedural"] = "semantic",
    content: str = "k",
    importance: float = 0.5,
) -> Any:
    from app.memory.consolidator import DistilledMemory

    return DistilledMemory(kind=kind, content=content, importance=importance)


def make_consolidator(
    db: AsyncMock,
    store: MagicMock,
    router: MagicMock,
    embedding: MagicMock,
    **kwargs: Any,
) -> Any:
    from app.memory.consolidator import Consolidator

    return Consolidator(db=db, store=store, router=router, embedding=embedding, **kwargs)


class TestRunOnce:
    async def test_no_batch_zero_llm_still_decays(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        store.list_unconsolidated = AsyncMock(return_value=[])
        store.decay_importance = AsyncMock(return_value=3)
        c = make_consolidator(db, store, router, embedding)

        result = await c.run_once(adapter=make_adapter())

        assert result == (0, 0, 0, 0, 3) or (
            result.processed == 0
            and result.distilled == 0
            and result.inserted == 0
            and result.merged == 0
            and result.decayed == 3
        )
        router.complete_structured.assert_not_called()
        store.mark_consolidated.assert_not_called()
        store.decay_importance.assert_awaited_once()

    async def test_no_default_adapter_raises_before_llm(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        store.list_unconsolidated = AsyncMock(return_value=[make_memory_row()])
        c = make_consolidator(db, store, router, embedding)

        from app.memory.consolidator import NoDefaultAdapterError

        with pytest.raises(NoDefaultAdapterError):
            await c.run_once(adapter=None)

        router.complete_structured.assert_not_called()
        store.mark_consolidated.assert_not_called()
        store.decay_importance.assert_not_called()

    async def test_distills_and_inserts(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        rows = [make_memory_row("e1"), make_memory_row("e2")]
        store.list_unconsolidated = AsyncMock(return_value=rows)
        router.complete_structured = AsyncMock(
            return_value=make_structured_response(
                [make_item("semantic", "k1"), make_item("procedural", "k2", 0.7)],
            )
        )
        embedding.embed_one = AsyncMock(return_value=VEC)
        store.search_vector = AsyncMock(return_value=[])
        store.store = AsyncMock()
        store.mark_consolidated = AsyncMock(return_value=2)
        store.decay_importance = AsyncMock(return_value=5)
        c = make_consolidator(db, store, router, embedding)

        result = await c.run_once(adapter=make_adapter())

        assert result.processed == 2
        assert result.distilled == 2
        assert result.inserted == 2
        assert result.merged == 0
        assert result.decayed == 5
        assert embedding.embed_one.await_count == 2
        assert store.store.await_count == 2
        store.mark_consolidated.assert_awaited_once()
        marked = store.mark_consolidated.await_args.kwargs
        assert {r.id for r in rows} == set(marked["ids"])

    async def test_merges_when_similar_hit(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        store.list_unconsolidated = AsyncMock(return_value=[make_memory_row()])
        router.complete_structured = AsyncMock(
            return_value=make_structured_response([make_item("semantic", "k1", 0.6)]),
        )
        embedding.embed_one = AsyncMock(return_value=VEC)
        from app.services.memory_service import MemoryHit

        existing = make_memory_row("old")
        existing.kind = "semantic"
        existing.importance = 0.5  # max(旧 0.5, 新 0.6) = 0.6，见 test_merge_importance_takes_max
        store.search_vector = AsyncMock(return_value=[MemoryHit(memory=existing, score=0.95)])
        store.merge_into = AsyncMock()
        store.mark_consolidated = AsyncMock(return_value=1)
        store.decay_importance = AsyncMock(return_value=0)
        c = make_consolidator(db, store, router, embedding)

        result = await c.run_once(adapter=make_adapter())

        assert result.inserted == 0
        assert result.merged == 1
        store.store.assert_not_called()
        store.merge_into.assert_awaited_once()
        kwargs = store.merge_into.await_args.kwargs
        assert kwargs["memory_id"] == existing.id
        assert kwargs["content"] == "k1"
        assert kwargs["embedding"] == VEC
        assert kwargs["importance"] == 0.6

    async def test_merge_importance_takes_max(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        store.list_unconsolidated = AsyncMock(return_value=[make_memory_row()])
        router.complete_structured = AsyncMock(
            return_value=make_structured_response([make_item("semantic", "k1", 0.3)]),
        )
        embedding.embed_one = AsyncMock(return_value=VEC)
        from app.services.memory_service import MemoryHit

        existing = make_memory_row("old")
        existing.importance = 0.9
        store.search_vector = AsyncMock(return_value=[MemoryHit(memory=existing, score=0.99)])
        store.merge_into = AsyncMock()
        store.mark_consolidated = AsyncMock(return_value=1)
        store.decay_importance = AsyncMock(return_value=0)
        c = make_consolidator(db, store, router, embedding)

        await c.run_once(adapter=make_adapter())

        assert store.merge_into.await_args.kwargs["importance"] == 0.9

    async def test_below_threshold_inserts(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        store.list_unconsolidated = AsyncMock(return_value=[make_memory_row()])
        router.complete_structured = AsyncMock(
            return_value=make_structured_response([make_item("semantic", "k1")]),
        )
        embedding.embed_one = AsyncMock(return_value=VEC)
        from app.services.memory_service import MemoryHit

        store.search_vector = AsyncMock(
            return_value=[MemoryHit(memory=make_memory_row(), score=0.899)],
        )
        store.store = AsyncMock()
        store.mark_consolidated = AsyncMock(return_value=1)
        store.decay_importance = AsyncMock(return_value=0)
        c = make_consolidator(db, store, router, embedding)

        result = await c.run_once(adapter=make_adapter())

        assert result.inserted == 1
        assert result.merged == 0
        store.merge_into.assert_not_called()

    async def test_empty_distill_still_consumes_batch(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        rows = [make_memory_row()]
        store.list_unconsolidated = AsyncMock(return_value=rows)
        router.complete_structured = AsyncMock(return_value=make_structured_response([]))
        store.mark_consolidated = AsyncMock(return_value=1)
        store.decay_importance = AsyncMock(return_value=0)
        c = make_consolidator(db, store, router, embedding)

        result = await c.run_once(adapter=make_adapter())

        assert result.processed == 1
        assert result.distilled == 0
        store.mark_consolidated.assert_awaited_once()
        embedding.embed_one.assert_not_called()

    async def test_llm_error_raises_and_skips_mark(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        store.list_unconsolidated = AsyncMock(return_value=[make_memory_row()])
        router.complete_structured = AsyncMock(
            side_effect=LLMTransportError("boom", model_id="m"),
        )
        c = make_consolidator(db, store, router, embedding)

        with pytest.raises(LLMTransportError):
            await c.run_once(adapter=make_adapter())

        store.mark_consolidated.assert_not_called()
        store.store.assert_not_called()

    async def test_embedding_error_raises_and_skips_mark(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        store.list_unconsolidated = AsyncMock(return_value=[make_memory_row()])
        router.complete_structured = AsyncMock(
            return_value=make_structured_response([make_item("semantic", "k1")]),
        )
        embedding.embed_one = AsyncMock(side_effect=EmbeddingError("boom"))
        c = make_consolidator(db, store, router, embedding)

        with pytest.raises(EmbeddingError):
            await c.run_once(adapter=make_adapter())

        store.mark_consolidated.assert_not_called()
        store.store.assert_not_called()

    async def test_prompt_includes_batch_contents(self) -> None:
        db, store, router, embedding = make_db(), make_store(), make_router(), make_embedding()
        rows = [make_memory_row("需求：A"), make_memory_row("需求：B")]
        store.list_unconsolidated = AsyncMock(return_value=rows)
        router.complete_structured = AsyncMock(return_value=make_structured_response([]))
        store.mark_consolidated = AsyncMock(return_value=2)
        store.decay_importance = AsyncMock(return_value=0)
        c = make_consolidator(db, store, router, embedding)

        await c.run_once(adapter=make_adapter())

        user_prompt = router.complete_structured.await_args.kwargs["user"]
        assert "需求：A" in user_prompt
        assert "需求：B" in user_prompt


class TestConsolidatorLoop:
    async def test_loop_runs_cycle_then_sleeps(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.memory.consolidator import consolidator_loop

        calls: list[float] = []
        cycle_count = 0

        async def fake_cycle() -> None:
            nonlocal cycle_count
            cycle_count += 1
            if cycle_count == 1:
                raise asyncio.CancelledError()

        async def fake_sleep(interval: float) -> None:
            calls.append(interval)

        monkeypatch.setattr("app.memory.consolidator.asyncio.sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await consolidator_loop(interval=42.0, run_cycle=fake_cycle)

        assert cycle_count == 1
        assert calls == [42.0]
