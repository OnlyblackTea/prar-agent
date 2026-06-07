"""Comment CRUD + 写入前置校验。"""
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.comment_schemas import CommentCreate
from app.core.logging import get_logger
from app.db.models import Comment, Plan, Session

_log = get_logger("comment_service")


class CommentNotFoundError(Exception):
    """评论不存在。"""


class CommentService:
    """评论持久化。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self, *, session_id: UUID, payload: CommentCreate,
    ) -> Comment:
        """写入评论，含 4 道前置校验。"""
        session = await self._db.get(Session, session_id)
        if session is None:
            from app.services.session_service import SessionNotFoundError
            raise SessionNotFoundError(str(session_id))

        if payload.plan_version > session.current_plan_version:
            raise ValueError("invalid_plan_version")

        if session.phase != "plan_review":
            raise ValueError("phase_not_review")

        # quote sanity check：避免脏数据进库
        plan = await self._get_plan(session_id, payload.plan_version)
        if not _quote_in_plan(payload.quote, plan.document):
            raise ValueError("quote_not_found_in_plan")

        comment = Comment(
            session_id=session_id,
            plan_version=payload.plan_version,
            anchor_id=payload.anchor_id,
            quote=payload.quote,
            quote_context=payload.quote_context,
            body=payload.body,
        )
        self._db.add(comment)
        await self._db.flush()
        await self._db.refresh(comment)
        _log.info(
            "comment_created",
            comment_id=str(comment.id),
            session_id=str(session_id),
        )
        return comment

    async def list_by_version(
        self, *, session_id: UUID, plan_version: int,
    ) -> list[Comment]:
        """按版本列出评论，created_at 升序。"""
        stmt = (
            select(Comment)
            .where(Comment.session_id == session_id)
            .where(Comment.plan_version == plan_version)
            .order_by(Comment.created_at.asc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def get(self, comment_id: UUID) -> Comment:
        c = await self._db.get(Comment, comment_id)
        if c is None:
            raise CommentNotFoundError(str(comment_id))
        return c

    async def _get_plan(self, session_id: UUID, version: int) -> Plan:
        stmt = (
            select(Plan)
            .where(Plan.session_id == session_id)
            .where(Plan.version == version)
        )
        result = await self._db.execute(stmt)
        plan = result.scalar_one_or_none()
        if plan is None:
            raise ValueError("invalid_plan_version")
        return plan


def _quote_in_plan(quote: str, document: dict) -> bool:
    """把 plan document 全部 text 拼起来判断 quote 是否子串。

    粗粒度但够用：Task 14 才上 fuzzy match。
    """
    nodes = document.get("nodes", [])
    full_text = "\n".join(_extract_text(n) for n in nodes)
    return quote in full_text


def _extract_text(node: dict) -> str:
    """从 plan node 抽 text 字段。"""
    if "text" in node:
        return node["text"]
    parts = [
        node.get(k, "")
        for k in ("question", "term", "definition", "description")
    ]
    return " ".join(p for p in parts if p)
