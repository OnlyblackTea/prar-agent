"""Memory 向量写入/检索服务（pgvector 余弦检索 + access 记账）。"""
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, update
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
    ) -> Memory:
        if kind not in VALID_KINDS:
            raise ValueError(f"invalid kind: {kind!r}")
        vec = await self._embedding.embed_one(content)
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
        if not 1 <= limit <= MAX_LIMIT:
            raise ValueError(f"limit must be in [1, {MAX_LIMIT}]")
        if kinds is not None:
            invalid = [k for k in kinds if k not in VALID_KINDS]
            if invalid:
                raise ValueError(f"invalid kind: {invalid[0]!r}")
        vec = await self._embedding.embed_one(query)

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
        rows = result.all()

        mem_ids = [mem.id for mem, _ in rows]
        if mem_ids:
            await self._db.execute(
                update(Memory)
                .where(Memory.id.in_(mem_ids))
                .values(
                    access_count=Memory.access_count + 1,
                    last_accessed=func.now(),
                )
            )
        return [MemoryHit(memory=mem, score=float(s)) for mem, s in rows]
