"""Comment CRUD + 写入前置校验。"""
from typing import Any, cast
from uuid import UUID

from sqlalchemy import select, update
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
            raise SessionNotFoundError(session_id)

        if payload.plan_version > session.current_plan_version:
            raise ValueError("invalid_plan_version")

        # action_review 也允许：27 号 D2 的 step 结果评论走同一张表
        if session.phase not in ("plan_review", "action_review"):
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

    async def list_unresolved(
        self, *, session_id: UUID, plan_version: int,
    ) -> list[Comment]:
        """按 session + plan_version 拉 resolved=false 的评论。"""
        stmt = (
            select(Comment)
            .where(Comment.session_id == session_id)
            .where(Comment.plan_version == plan_version)
            .where(Comment.resolved.is_(False))
            .order_by(Comment.created_at.asc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())

    async def mark_resolved(self, comment_ids: list[UUID]) -> None:
        """批量标 resolved=true。id 不存在时 UPDATE 自然 no-op。"""
        if not comment_ids:
            return
        stmt = (
            update(Comment)
            .where(Comment.id.in_(comment_ids))
            .values(resolved=True)
        )
        await self._db.execute(stmt)

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


def _quote_in_plan(quote: str, document: dict[str, Any]) -> bool:
    """把 plan document 全部 text 拼起来判断 quote 是否子串。

    粗粒度但够用：Task 14 才上 fuzzy match。
    """
    nodes = document.get("nodes", [])
    full_text = "\n".join(_extract_text(n) for n in nodes)
    return quote in full_text


def _extract_text(node: dict[str, Any]) -> str:
    """从 plan node 抽 text 字段。"""
    if "text" in node:
        return cast(str, node["text"])
    parts = [
        node.get(k, "")
        for k in ("title", "question", "term", "definition", "description")
    ]
    return " ".join(p for p in parts if p)
