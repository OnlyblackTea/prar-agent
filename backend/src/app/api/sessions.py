"""Session CRUD + Decision 答题 + 推进阶段 API。"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.state_machine import InvalidTransitionError
from app.db.session import get_db
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
    document: dict

    model_config = {"from_attributes": True}


class AnswerDecisionRequest(BaseModel):
    answer: str


class AnswerDecisionResponse(BaseModel):
    all_blocking_answered: bool


class AdvanceToActingResponse(BaseModel):
    phase: str


class ErrorDetail(BaseModel):
    code: str
    message: str


# ===== Dependency =====


async def get_session_service(
    db: AsyncSession = Depends(get_db),
) -> SessionService:
    return SessionService(db)


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
