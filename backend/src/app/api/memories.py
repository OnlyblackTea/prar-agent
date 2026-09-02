"""Memory 向量写入/检索路由。embedding 是内部数据，永不出 API。"""
from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.embedding import (
    EmbeddingDimensionError,
    EmbeddingError,
    get_embedding_service,
)
from app.db.session import get_db
from app.llm.router import LLMError, LLMRouter
from app.llm.types import ResolvedAdapter
from app.memory.consolidator import (
    Consolidator,
    NoDefaultAdapterError,
    resolve_default_adapter,
)
from app.services.memory_service import MemoryService

MemoryKind = Literal["episodic", "semantic", "procedural"]

router = APIRouter(prefix="/api/memories", tags=["memories"])


class MemoryCreate(BaseModel):
    kind: MemoryKind
    content: str = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    user_id: UUID | None = None
    source_session: UUID | None = None


class MemoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    kind: str
    content: str
    importance: float
    user_id: UUID | None
    source_session: UUID | None
    last_accessed: datetime
    access_count: int
    created_at: datetime


class MemoryHitResponse(BaseModel):
    id: UUID
    kind: str
    content: str
    importance: float
    source_session: UUID | None
    last_accessed: datetime
    access_count: int
    created_at: datetime
    score: float


class MemorySearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=50)
    kinds: list[MemoryKind] | None = None


class MemorySearchResponse(BaseModel):
    hits: list[MemoryHitResponse]


class ConsolidateResponse(BaseModel):
    processed: int
    distilled: int
    inserted: int
    merged: int
    decayed: int


async def get_memory_service(db: AsyncSession = Depends(get_db)) -> MemoryService:
    return MemoryService(db, get_embedding_service())


def get_router_dep() -> LLMRouter:
    """复用 ws_plan.get_router 的 lru_cache 单例（M1-10a-fixup 已建立）。"""
    from app.api.ws_plan import get_router
    return get_router()


async def get_consolidator(
    db: AsyncSession = Depends(get_db),
    router: LLMRouter = Depends(get_router_dep),
) -> Consolidator:
    return Consolidator(
        db=db,
        store=MemoryService(db, get_embedding_service()),
        router=router,
        embedding=get_embedding_service(),
    )


async def get_default_adapter(
    db: AsyncSession = Depends(get_db),
) -> ResolvedAdapter | None:
    return await resolve_default_adapter(db)


@router.post("", response_model=MemoryResponse, status_code=201)
async def create_memory(
    payload: MemoryCreate,
    service: MemoryService = Depends(get_memory_service),
) -> MemoryResponse:
    try:
        mem = await service.store(
            kind=payload.kind,
            content=payload.content,
            importance=payload.importance,
            user_id=payload.user_id,
            source_session=payload.source_session,
        )
    except EmbeddingDimensionError as e:
        raise HTTPException(
            status_code=502, detail="embedding_dimension_mismatch",
        ) from e
    except EmbeddingError as e:
        raise HTTPException(status_code=502, detail="embedding_failed") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return MemoryResponse.model_validate(mem)


@router.post("/search", response_model=MemorySearchResponse)
async def search_memories(
    payload: MemorySearchRequest,
    service: MemoryService = Depends(get_memory_service),
) -> MemorySearchResponse:
    try:
        hits = await service.search(
            query=payload.query,
            limit=payload.limit,
            kinds=payload.kinds,
        )
    except EmbeddingDimensionError as e:
        raise HTTPException(
            status_code=502, detail="embedding_dimension_mismatch",
        ) from e
    except EmbeddingError as e:
        raise HTTPException(status_code=502, detail="embedding_failed") from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return MemorySearchResponse(
        hits=[
            MemoryHitResponse(
                id=h.memory.id,
                kind=h.memory.kind,
                content=h.memory.content,
                importance=h.memory.importance,
                source_session=h.memory.source_session,
                last_accessed=h.memory.last_accessed,
                access_count=h.memory.access_count,
                created_at=h.memory.created_at,
                score=h.score,
            )
            for h in hits
        ]
    )


@router.post("/consolidate", response_model=ConsolidateResponse)
async def consolidate_memories(
    consolidator: Consolidator = Depends(get_consolidator),
    adapter: ResolvedAdapter | None = Depends(get_default_adapter),
) -> ConsolidateResponse:
    try:
        result = await consolidator.run_once(adapter=adapter)
    except NoDefaultAdapterError as e:
        raise HTTPException(status_code=503, detail="no_default_adapter") from e
    except LLMError as e:
        raise HTTPException(status_code=502, detail="llm_failed") from e
    except EmbeddingError as e:
        raise HTTPException(status_code=502, detail="embedding_failed") from e
    return ConsolidateResponse(
        processed=result.processed,
        distilled=result.distilled,
        inserted=result.inserted,
        merged=result.merged,
        decayed=result.decayed,
    )
