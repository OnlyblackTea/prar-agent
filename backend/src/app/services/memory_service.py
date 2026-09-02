"""Memory 向量写入/检索服务（pgvector 余弦检索 + access 记账 + Consolidator 支撑）。"""
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID

from sqlalchemy import CursorResult, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.embedding import EmbeddingService
from app.core.logging import get_logger
from app.db.models import Memory

_log = get_logger("memory_service")

VALID_KINDS = {"episodic", "semantic", "procedural"}
DEFAULT_LIMIT = 5
MAX_LIMIT = 50


@dataclass(frozen=True, slots=True)
class MemoryHit:
    """检索命中：记忆行 + 余弦相似度 score（1 = 完全相同方向）。"""

    memory: Memory
    score: float


class MemoryService:
    """写入：嵌入成功后整行落库（嵌入失败零部分写入）；检索：余弦排序 + 命中记账。"""

    def __init__(self, db: AsyncSession, embedding: EmbeddingService) -> None:
        self._db = db
        self._embedding = embedding

    async def store(
        self,
        *,
        kind: str,
        content: str,
        importance: float = 0.5,
        user_id: UUID | None = None,
        source_session: UUID | None = None,
        embedding: list[float] | None = None,
    ) -> Memory:
        if kind not in VALID_KINDS:
            raise ValueError(f"invalid kind: {kind!r}")
        vec = (
            embedding
            if embedding is not None
            else await self._embedding.embed_one(content)
        )
        mem = Memory(
            kind=kind,
            content=content,
            embedding=vec,
            importance=importance,
            user_id=user_id,
            source_session=source_session,
        )
        self._db.add(mem)
        await self._db.flush()
        await self._db.refresh(mem)
        _log.info("memory_stored", memory_id=str(mem.id), kind=kind)
        return mem

    async def search(
        self,
        *,
        query: str,
        limit: int = DEFAULT_LIMIT,
        kinds: Sequence[str] | None = None,
    ) -> list[MemoryHit]:
        if not query.strip():
            raise ValueError("query must not be blank")
        vec = await self._embedding.embed_one(query)
        hits = await self.search_vector(vec=vec, limit=limit, kinds=kinds)

        mem_ids = [h.memory.id for h in hits]
        if mem_ids:
            await self._db.execute(
                update(Memory)
                .where(Memory.id.in_(mem_ids))
                .values(
                    access_count=Memory.access_count + 1,
                    last_accessed=func.now(),
                )
            )
        return hits

    async def search_vector(
        self,
        *,
        vec: list[float],
        limit: int = DEFAULT_LIMIT,
        kinds: Sequence[str] | None = None,
    ) -> list[MemoryHit]:
        """按向量余弦检索。不记账、不 embed——供合并去重复用已有向量。"""
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be in [1, {MAX_LIMIT}]")
        if kinds is not None:
            invalid = [k for k in kinds if k not in VALID_KINDS]
            if invalid:
                raise ValueError(f"invalid kind: {invalid[0]!r}")

        distance = Memory.embedding.cosine_distance(vec)
        stmt = (
            select(Memory, (1 - distance).label("score"))
            .where(Memory.embedding.isnot(None))
            .order_by(distance)
            .limit(limit)
        )
        if kinds is not None:
            stmt = stmt.where(Memory.kind.in_(kinds))
        result = await self._db.execute(stmt)
        return [
            MemoryHit(memory=mem, score=float(s)) for mem, s in result.all()
        ]

    async def merge_into(
        self,
        *,
        memory_id: UUID,
        content: str,
        embedding: list[float],
        importance: float,
    ) -> None:
        """合并去重：新提炼内容覆盖旧行（提炼结果语义上是更综合的知识）。"""
        await self._db.execute(
            update(Memory)
            .where(Memory.id == memory_id)
            .values(
                content=content,
                embedding=embedding,
                importance=importance,
            )
        )
        _log.info("memory_merged", memory_id=str(memory_id))

    async def list_unconsolidated(
        self,
        *,
        limit: int,
        for_update: bool = False,
    ) -> list[Memory]:
        """取未消费的 episodic 原料（consumed 标记 = consolidated_at IS NULL）。"""
        stmt = (
            select(Memory)
            .where(Memory.kind == "episodic", Memory.consolidated_at.is_(None))
            .order_by(Memory.created_at)
            .limit(limit)
        )
        if for_update:
            stmt = stmt.with_for_update()
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def mark_consolidated(self, *, ids: Sequence[UUID]) -> int:
        if not ids:
            return 0
        result = cast(
            CursorResult[Any],
            await self._db.execute(
                update(Memory)
                .where(Memory.id.in_(ids))
                .values(consolidated_at=func.now())
            ),
        )
        return int(result.rowcount or 0)

    async def decay_importance(
        self,
        *,
        kinds: Sequence[str],
        factor: float,
        floor: float,
    ) -> int:
        """对指定 kind 的行做 importance 衰减（GREATEST 保下限）。episodic 不衰减。"""
        result = cast(
            CursorResult[Any],
            await self._db.execute(
                update(Memory)
                .where(Memory.kind.in_(kinds))
                .values(importance=func.greatest(floor, Memory.importance * factor))
            ),
        )
        return int(result.rowcount or 0)
