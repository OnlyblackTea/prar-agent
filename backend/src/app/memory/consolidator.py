"""Consolidator：episodic 原料 → 一次 LLM 提炼 → semantic/procedural 落库（合并去重）+ 衰减。

事务边界：run_once 只 flush 不 commit，由调用方提交/回滚
（API 走 get_db，后台循环走 sessionmaker + 显式 rollback）。
LLM/embedding 失败异常上抛：本轮不标记、零部分写入，下轮可重试。
"""
import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Literal, NamedTuple

from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.embedding import EmbeddingService
from app.core.logging import get_logger
from app.db.models import Memory
from app.llm.prompts.loader import load_prompt
from app.llm.router import LLMRouter
from app.llm.types import ResolvedAdapter
from app.services.adapter_service import AdapterService
from app.services.memory_service import MemoryService

_log = get_logger("consolidator")

DECAY_KINDS = ("semantic", "procedural")

_SYSTEM_PROMPT = (
    "你是 PRAR-Agent 长期记忆 Consolidator。"
    "严格按给定的 JSON Schema 提炼知识，无把握时输出空列表。"
)


class NoDefaultAdapterError(Exception):
    """无 is_default adapter 配置，本轮无法提炼（不标记不写入）。"""


class DistilledMemory(BaseModel):
    kind: Literal["semantic", "procedural"]
    content: str = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)


class ConsolidatedMemories(BaseModel):
    items: list[DistilledMemory]


class ConsolidateResult(NamedTuple):
    processed: int
    distilled: int
    inserted: int
    merged: int
    decayed: int


def _episodic_block(batch: Sequence[Memory]) -> str:
    return "\n".join(f"[{i}] {m.content}" for i, m in enumerate(batch, start=1))


async def resolve_default_adapter(db: AsyncSession) -> ResolvedAdapter | None:
    """取 DB 默认 adapter 并解析凭据（resolve 失败抛 LLMTransportError）。"""
    service = AdapterService(db)
    adapter = await service.get_default()
    if adapter is None:
        return None
    return service.resolve(adapter)


class Consolidator:
    def __init__(
        self,
        *,
        db: AsyncSession,
        store: MemoryService,
        router: LLMRouter,
        embedding: EmbeddingService,
        batch_size: int = 20,
        merge_threshold: float = 0.9,
        decay_factor: float = 0.9,
        decay_floor: float = 0.1,
    ) -> None:
        self._db = db
        self._store = store
        self._router = router
        self._embedding = embedding
        self._batch_size = batch_size
        self._merge_threshold = merge_threshold
        self._decay_factor = decay_factor
        self._decay_floor = decay_floor

    async def run_once(
        self, *, adapter: ResolvedAdapter | None,
    ) -> ConsolidateResult:
        batch = await self._store.list_unconsolidated(
            limit=self._batch_size, for_update=True,
        )
        processed = len(batch)
        distilled = inserted = merged = 0

        if batch:
            if adapter is None:
                raise NoDefaultAdapterError("no default adapter configured")
            user_prompt = load_prompt("consolidator.md").format(
                episodic_block=_episodic_block(batch),
            )
            response = await self._router.complete_structured(
                adapter=adapter,
                system=_SYSTEM_PROMPT,
                user=user_prompt,
                schema=ConsolidatedMemories,
            )
            items = response.parsed.items
            distilled = len(items)
            for item in items:
                vec = await self._embedding.embed_one(item.content)
                hits = await self._store.search_vector(
                    vec=vec, kinds=[item.kind], limit=1,
                )
                if hits and hits[0].score >= self._merge_threshold:
                    merged += 1
                    await self._store.merge_into(
                        memory_id=hits[0].memory.id,
                        content=item.content,
                        embedding=vec,
                        importance=max(
                            hits[0].memory.importance, item.importance,
                        ),
                    )
                else:
                    inserted += 1
                    await self._store.store(
                        kind=item.kind,
                        content=item.content,
                        embedding=vec,
                        importance=item.importance,
                    )
            await self._store.mark_consolidated(ids=[m.id for m in batch])

        decayed = await self._store.decay_importance(
            kinds=DECAY_KINDS,
            factor=self._decay_factor,
            floor=self._decay_floor,
        )
        result = ConsolidateResult(
            processed=processed,
            distilled=distilled,
            inserted=inserted,
            merged=merged,
            decayed=decayed,
        )
        _log.info(
            "consolidator_round",
            processed=processed,
            distilled=distilled,
            inserted=inserted,
            merged=merged,
            decayed=decayed,
        )
        return result


async def consolidator_loop(
    interval: float,
    run_cycle: Callable[[], Awaitable[None]],
) -> None:
    """后台循环：先睡 interval 再跑一轮（启动消化由调用方进循环前跑一次）。"""
    while True:
        await asyncio.sleep(interval)
        await run_cycle()
