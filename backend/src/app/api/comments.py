"""Comment CRUD 路由。"""
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.comment_schemas import CommentCreate, CommentResponse
from app.db.session import get_db
from app.services.comment_service import CommentNotFoundError, CommentService
from app.services.session_service import SessionNotFoundError

router = APIRouter(prefix="/api/sessions", tags=["comments"])


async def get_comment_service(
    db: AsyncSession = Depends(get_db),
) -> CommentService:
    return CommentService(db)


@router.post(
    "/{session_id}/comments",
    response_model=CommentResponse,
    status_code=201,
)
async def create_comment(
    session_id: UUID,
    payload: CommentCreate,
    service: CommentService = Depends(get_comment_service),
) -> CommentResponse:
    try:
        c = await service.create(session_id=session_id, payload=payload)
    except SessionNotFoundError as e:
        raise HTTPException(status_code=404, detail="session_not_found") from e
    except ValueError as e:
        msg = str(e)
        status = 409 if msg == "phase_not_review" else 400
        raise HTTPException(status_code=status, detail=msg) from e
    return CommentResponse.model_validate(c)


@router.get(
    "/{session_id}/comments",
    response_model=list[CommentResponse],
)
async def list_comments(
    session_id: UUID,
    plan_version: int = Query(ge=1),
    service: CommentService = Depends(get_comment_service),
) -> list[CommentResponse]:
    comments = await service.list_by_version(
        session_id=session_id, plan_version=plan_version,
    )
    return [CommentResponse.model_validate(c) for c in comments]


@router.get(
    "/{session_id}/comments/{comment_id}",
    response_model=CommentResponse,
)
async def get_comment(
    session_id: UUID,
    comment_id: UUID,
    service: CommentService = Depends(get_comment_service),
) -> CommentResponse:
    try:
        c = await service.get(comment_id)
    except CommentNotFoundError as e:
        raise HTTPException(status_code=404, detail="comment_not_found") from e
    if c.session_id != session_id:
        raise HTTPException(status_code=404, detail="comment_not_found")
    return CommentResponse.model_validate(c)
