"""Session CRUD + Decision 答题 + 推进阶段 + Review Merge + 版本查询 API。"""

from datetime import datetime
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.embedding import (
    EmbeddingDimensionError,
    EmbeddingError,
    get_embedding_service,
)
from app.core.merger_schemas import MergerResult
from app.core.plan_schemas import PlanDocument
from app.core.state_machine import InvalidTransitionError
from app.db.session import get_db
from app.llm.router import LLMRouter
from app.memory.long_term import LongTermMemory
from app.services.memory_service import MemoryService
from app.services.session_service import SessionNotFoundError, SessionService

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


# ===== Pydantic Schemas =====


class CreateSessionRequest(BaseModel):
    init_request: str = Field(min_length=1)
    adapter_id: UUID


class SessionResponse(BaseModel):
    id: UUID
    init_request: str
    phase: str
    current_plan_version: int
    adapter_id: UUID

    model_config = {"from_attributes": True}


class PlanResponse(BaseModel):
    id: UUID
    session_id: UUID
    version: int
    document: dict[str, Any]

    model_config = {"from_attributes": True}


class PlanSummary(BaseModel):
    version: int
    node_count: int
    created_at: datetime


class PlanListResponse(BaseModel):
    session_id: UUID
    current_version: int
    versions: list[PlanSummary]


class AnswerDecisionRequest(BaseModel):
    answer: str


class AnswerDecisionResponse(BaseModel):
    all_blocking_answered: bool


class AdvanceToActingResponse(BaseModel):
    phase: str


class ErrorDetail(BaseModel):
    code: str
    message: str


class MergeResponse(BaseModel):
    plan_version: int
    plan: PlanDocument  # 新版本（或全 reject 时的原版本）完整 doc
    merger_result: MergerResult
    plan_changed: bool  # accepted_ids 非空时为 True


# ===== Dependency =====


async def get_session_service(
    db: AsyncSession = Depends(get_db),
) -> SessionService:
    return SessionService(db)


async def get_long_term(db: AsyncSession = Depends(get_db)) -> LongTermMemory:
    return LongTermMemory(MemoryService(db, get_embedding_service()))


def get_router_dep() -> LLMRouter:
    """复用 ws_plan.get_router 的 lru_cache 单例（M1-10a-fixup 已建立）。"""
    from app.api.ws_plan import get_router
    return get_router()


# ===== Routes =====


@router.post("", response_model=SessionResponse, status_code=201)
async def create_session(
    payload: CreateSessionRequest,
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    try:
        s = await service.create(
            init_request=payload.init_request,
            adapter_id=payload.adapter_id,
        )
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return SessionResponse.model_validate(s)


@router.get("/{session_id}", response_model=SessionResponse)
async def get_session(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
) -> SessionResponse:
    try:
        s = await service.get(session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail="session_not_found") from e
    return SessionResponse.model_validate(s)


@router.get("/{session_id}/plan", response_model=PlanResponse)
async def get_current_plan(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
) -> PlanResponse:
    try:
        plan = await service.get_current_plan(session_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return PlanResponse.model_validate(plan)


@router.get("/{session_id}/plans", response_model=PlanListResponse)
async def list_plans(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
) -> PlanListResponse:
    try:
        s, plans = await service.list_plans(session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail="session_not_found") from e
    return PlanListResponse(
        session_id=s.id,
        current_version=s.current_plan_version,
        versions=[
            PlanSummary(
                version=p.version,
                node_count=len(p.document.get("nodes", [])),
                created_at=p.created_at,
            )
            for p in plans
        ],
    )


@router.get("/{session_id}/plans/{version}", response_model=PlanResponse)
async def get_plan_version(
    session_id: UUID,
    version: int,
    service: SessionService = Depends(get_session_service),
) -> PlanResponse:
    try:
        plan = await service.get_plan(session_id, version)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail="session_not_found") from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return PlanResponse.model_validate(plan)


@router.post(
    "/{session_id}/decisions/{decision_id}",
    response_model=AnswerDecisionResponse,
)
async def answer_decision(
    session_id: UUID,
    decision_id: str,
    payload: AnswerDecisionRequest,
    service: SessionService = Depends(get_session_service),
) -> AnswerDecisionResponse:
    try:
        all_answered = await service.answer_decision(
            session_id=session_id,
            decision_id=decision_id,
            answer=payload.answer,
        )
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail="session_not_found") from e
    except ValueError as e:
        msg = str(e)
        if "not in options" in msg:
            raise HTTPException(status_code=400, detail="answer_invalid") from e
        if "not found" in msg:
            raise HTTPException(status_code=404, detail="decision_not_found") from e
        raise HTTPException(status_code=400, detail=msg) from e
    return AnswerDecisionResponse(all_blocking_answered=all_answered)


@router.post(
    "/{session_id}/advance-to-acting",
    response_model=AdvanceToActingResponse,
)
async def advance_to_acting(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
) -> AdvanceToActingResponse:
    try:
        s = await service.advance_to_acting(session_id)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail="session_not_found") from e
    except ValueError as e:
        raise HTTPException(status_code=409, detail="blocking_unanswered") from e
    except InvalidTransitionError as e:
        raise HTTPException(
            status_code=409, detail="illegal_phase_transition",
        ) from e
    return AdvanceToActingResponse(phase=s.phase)


@router.post("/{session_id}/complete", response_model=SessionResponse)
async def complete_session(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
    long_term: LongTermMemory = Depends(get_long_term),
) -> SessionResponse:
    try:
        s = await service.complete(session_id=session_id, long_term=long_term)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail="session_not_found") from e
    except InvalidTransitionError as e:
        raise HTTPException(
            status_code=409, detail="illegal_phase_transition",
        ) from e
    except EmbeddingDimensionError as e:
        raise HTTPException(
            status_code=502, detail="embedding_dimension_mismatch",
        ) from e
    except EmbeddingError as e:
        raise HTTPException(status_code=502, detail="embedding_failed") from e
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e)) from e
    return SessionResponse.model_validate(s)


@router.post("/{session_id}/merge", response_model=MergeResponse)
async def merge_plan_endpoint(
    session_id: UUID,
    service: SessionService = Depends(get_session_service),
    llm_router: LLMRouter = Depends(get_router_dep),
) -> MergeResponse:
    try:
        new_plan_doc, merger_result, new_version = await service.merge_plan(
            session_id=session_id, router=llm_router,
        )
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail="session_not_found") from e
    except ValueError as e:
        msg = str(e)
        status = 409 if msg == "phase_not_review" else 400
        raise HTTPException(status_code=status, detail=msg) from e
    # LLM 错误向上冒：transport / structured_output → 500
    return MergeResponse(
        plan_version=new_version,
        plan=new_plan_doc,
        merger_result=merger_result,
        plan_changed=any(
            a.decision in ("accept", "partial") for a in merger_result.actions
        ),
    )
