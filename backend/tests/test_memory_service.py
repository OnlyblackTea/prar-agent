"""MemoryService 单元测试 — mock AsyncSession + mock EmbeddingService，不真连 DB/API。"""
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.embedding import EmbeddingService, EmbeddingTransportError
from app.db.models import Memory
from app.services.memory_service import MemoryService

VEC = [0.1, 0.2, 0.3, 0.4]


def make_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()  # Session.add 是同步方法，AsyncMock 会误生成协程
    db.refresh = AsyncMock()
    return db


def make_embedding() -> MagicMock:
    return MagicMock(spec=EmbeddingService)


def make_memory_row(content: str = "hello") -> MagicMock:
    mem = MagicMock(spec=Memory)
    mem.id = uuid4()
    mem.kind = "episodic"
    mem.content = content
    mem.importance = 0.5
    mem.last_accessed = datetime.now(UTC)
    mem.access_count = 0
    mem.source_session = None
    mem.created_at = datetime.now(UTC)
    return mem


class TestStore:
    async def test_store_embeds_and_persists(self) -> None:
        db = make_db()
        emb = make_embedding()
        emb.embed_one = AsyncMock(return_value=VEC)
        svc = MemoryService(db, emb)

        mem = await svc.store(kind="episodic", content="hello")

        emb.embed_one.assert_awaited_once_with("hello")
        db.add.assert_called_once()
        row = db.add.call_args.args[0]
        assert row.kind == "episodic"
        assert row.content == "hello"
        assert row.embedding == VEC
        assert row.importance == 0.5
        assert row.user_id is None
        assert row.source_session is None
        db.flush.assert_awaited_once()
        db.refresh.assert_awaited_once()
        assert mem is row

    async def test_store_passes_optional_fields(self) -> None:
        db = make_db()
        emb = make_embedding()
        emb.embed_one = AsyncMock(return_value=VEC)
        svc = MemoryService(db, emb)
        uid = uuid4()
        sid = uuid4()

        await svc.store(
            kind="semantic",
            content="x",
            importance=0.9,
            user_id=uid,
            source_session=sid,
        )

        row = db.add.call_args.args[0]
        assert row.kind == "semantic"
        assert row.importance == 0.9
        assert row.user_id == uid
        assert row.source_session == sid

    async def test_store_invalid_kind_raises(self) -> None:
        db = make_db()
        emb = make_embedding()
        svc = MemoryService(db, emb)

        with pytest.raises(ValueError, match="kind"):
            await svc.store(kind="hacker", content="x")
        emb.embed_one.assert_not_called()
        db.add.assert_not_called()

    async def test_store_embedding_failure_no_partial_write(self) -> None:
        db = make_db()
        emb = make_embedding()
        emb.embed_one = AsyncMock(
            side_effect=EmbeddingTransportError("boom", model_id="m")
        )
        svc = MemoryService(db, emb)

        with pytest.raises(EmbeddingTransportError):
            await svc.store(kind="episodic", content="x")
        db.add.assert_not_called()
        db.flush.assert_not_called()


class TestSearch:
    async def test_search_returns_ordered_hits_and_bumps_access(self) -> None:
        db = make_db()
        emb = make_embedding()
        emb.embed_one = AsyncMock(return_value=VEC)
        rows = [
            (make_memory_row("near"), 0.95),
            (make_memory_row("far"), 0.4),
        ]
        result = MagicMock()
        result.all.return_value = rows
        db.execute = AsyncMock(return_value=result)
        svc = MemoryService(db, emb)

        hits = await svc.search(query="hello", limit=5)

        assert [h.score for h in hits] == [0.95, 0.4]
        assert [h.memory.content for h in hits] == ["near", "far"]
        emb.embed_one.assert_awaited_once_with("hello")
        assert db.execute.await_count == 2

    async def test_search_no_hits_no_bump(self) -> None:
        db = make_db()
        emb = make_embedding()
        emb.embed_one = AsyncMock(return_value=VEC)
        result = MagicMock()
        result.all.return_value = []
        db.execute = AsyncMock(return_value=result)
        svc = MemoryService(db, emb)

        hits = await svc.search(query="x")

        assert hits == []
        assert db.execute.await_count == 1

    async def test_search_kind_filter_in_sql(self) -> None:
        db = make_db()
        emb = make_embedding()
        emb.embed_one = AsyncMock(return_value=VEC)
        result = MagicMock()
        result.all.return_value = [(make_memory_row(), 0.9)]
        db.execute = AsyncMock(return_value=result)
        svc = MemoryService(db, emb)

        await svc.search(query="x", kinds=["procedural"])

        stmt: Any = db.execute.await_args_list[0].args[0]
        assert "kind IN" in str(stmt)

    async def test_search_blank_query_raises(self) -> None:
        db = make_db()
        svc = MemoryService(db, make_embedding())

        with pytest.raises(ValueError):
            await svc.search(query="   ")

    async def test_search_invalid_limit_raises(self) -> None:
        db = make_db()
        svc = MemoryService(db, make_embedding())

        with pytest.raises(ValueError):
            await svc.search(query="x", limit=0)
        with pytest.raises(ValueError):
            await svc.search(query="x", limit=51)

    async def test_search_embedding_failure_propagates(self) -> None:
        db = make_db()
        emb = make_embedding()
        emb.embed_one = AsyncMock(
            side_effect=EmbeddingTransportError("boom", model_id="m")
        )
        svc = MemoryService(db, emb)

        with pytest.raises(EmbeddingTransportError):
            await svc.search(query="x")
        db.execute.assert_not_called()
