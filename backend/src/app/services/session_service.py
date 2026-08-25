"""Session 服务层：创建 session、管理 plan、答题、推进阶段。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.merger_schemas import MergerResult
from app.core.plan_schemas import PlanDocument
from app.core.review_merger import ReviewMerger
from app.core.state_machine import Phase, transition
from app.db import models
from app.llm.router import LLMRouter
from app.services.adapter_service import AdapterService
from app.services.comment_service import CommentService

_log = get_logger("session_service")


class SessionNotFoundError(Exception):
    def __init__(self, session_id: UUID) -> None:
        super().__init__(f"Session {session_id} not found")
        self.session_id = session_id


class SessionService:
    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def create(
        self, *, init_request: str, adapter_id: UUID,
    ) -> models.Session:
        s = models.Session(
            init_request=init_request,
            adapter_id=adapter_id,
            phase=Phase.PLANNING.value,
        )
        self._db.add(s)
        await self._db.flush()
        _log.info("session_created", session_id=str(s.id))
        return s

    async def get(self, session_id: UUID) -> models.Session:
        s = await self._db.get(models.Session, session_id)
        if s is None:
            raise SessionNotFoundError(session_id)
        return s

    async def save_plan(
        self, *, session_id: UUID, plan: PlanDocument,
    ) -> models.Plan:
        s = await self.get(session_id)
        new_version = s.current_plan_version + 1
        p = models.Plan(
            session_id=session_id,
            version=new_version,
            document=plan.model_dump(),
        )
        self._db.add(p)
        s.current_plan_version = new_version
        transition(
            Phase(s.phase), Phase.PLAN_REVIEW, session_id=str(session_id),
        )
        s.phase = Phase.PLAN_REVIEW.value
        await self._db.flush()
        _log.info(
            "plan_saved",
            session_id=str(session_id),
            version=new_version,
            node_count=len(plan.nodes),
        )
        return p

    async def get_current_plan(self, session_id: UUID) -> models.Plan:
        from sqlalchemy import select

        stmt = (
            select(models.Plan)
            .where(models.Plan.session_id == session_id)
            .order_by(models.Plan.version.desc())
            .limit(1)
        )
        result = await self._db.execute(stmt)
        plan = result.scalar_one_or_none()
        if plan is None:
            raise ValueError(f"No plan for session {session_id}")
        return plan

    async def list_plans(
        self, session_id: UUID,
    ) -> tuple[models.Session, list[models.Plan]]:
        """全版本历史（升序），供前端版本选择器；无 plan 返回空列表。"""
        from sqlalchemy import select

        s = await self.get(session_id)
        stmt = (
            select(models.Plan)
            .where(models.Plan.session_id == session_id)
            .order_by(models.Plan.version.asc())
        )
        result = await self._db.execute(stmt)
        return s, list(result.scalars().all())

    async def get_plan(self, session_id: UUID, version: int) -> models.Plan:
        """按版本取 plan（Task 13 公开入口）；版本不存在报专用错码。"""
        await self.get(session_id)
        try:
            return await self._get_plan(session_id, version)
        except ValueError as e:
            raise ValueError("plan_version_not_found") from e

    async def answer_decision(
        self, *, session_id: UUID, decision_id: str, answer: str,
    ) -> bool:
        plan = await self.get_current_plan(session_id)
        doc: dict = dict(plan.document)
        nodes: list[dict] = list(doc.get("nodes", []))
        found = False
        for n in nodes:
            if n.get("type") == "decision" and n.get("id") == decision_id:
                if answer not in n.get("options", []):
                    raise ValueError(f"answer {answer!r} not in options")
                n["answer"] = answer
                found = True
                break
        if not found:
            raise ValueError(f"decision {decision_id!r} not found")
        plan.document = doc
        await self._db.flush()
        _log.info(
            "decision_answered",
            session_id=str(session_id),
            decision_id=decision_id,
            answer=answer,
        )
        return self._all_blocking_answered(nodes)

    @staticmethod
    def _all_blocking_answered(nodes: list[dict]) -> bool:
        for n in nodes:
            if (
                n.get("type") == "decision"
                and n.get("blocking")
                and n.get("answer") is None
            ):
                return False
        return True

    async def merge_plan(
        self,
        *,
        session_id: UUID,
        router: LLMRouter,
    ) -> tuple[PlanDocument, MergerResult, int]:
        """编排：拉评论 → 调 Merger → 落新 plan version → 标 resolved。

        返回 (new_plan, merger_result, new_plan_version)。
        全 reject 时不落新版本，返回原 plan 与当前 version。
        """
        s = await self.get(session_id)
        if s.phase != "plan_review":
            raise ValueError("phase_not_review")

        current_version = s.current_plan_version
        plan = await self._get_plan(session_id, current_version)
        comment_service = CommentService(self._db)
        comments = await comment_service.list_unresolved(
            session_id=session_id, plan_version=current_version,
        )
        if not comments:
            raise ValueError("no_comments_to_merge")

        adapter_service = AdapterService(self._db)
        db_adapter = await adapter_service.get(s.adapter_id)
        adapter = adapter_service.resolve(db_adapter)
        merger = ReviewMerger(router)
        new_plan_doc, merger_result = await merger.merge(
            plan=PlanDocument.model_validate(plan.document),
            comments=comments,
            adapter=adapter,
        )

        # 标 resolved 仅收 accept/partial；LLM 编错 id 时 UPDATE 自然 no-op
        accepted_ids = [
            a.comment_id for a in merger_result.actions
            if a.decision in ("accept", "partial")
        ]
        if not accepted_ids:
            # 全 reject：不落新版本，用户继续在原版上评论
            _log.info(
                "merge_all_rejected",
                session_id=str(session_id),
                version=current_version,
            )
            return (
                PlanDocument.model_validate(plan.document),
                merger_result,
                current_version,
            )

        new_version = current_version + 1
        new_plan = models.Plan(
            session_id=session_id,
            version=new_version,
            document=new_plan_doc.model_dump(mode="json"),
        )
        self._db.add(new_plan)
        s.current_plan_version = new_version
        await comment_service.mark_resolved(accepted_ids)
        await self._db.flush()
        _log.info(
            "plan_merged",
            session_id=str(session_id),
            from_version=current_version,
            to_version=new_version,
            actions_count=len(merger_result.actions),
            accepted_count=len(accepted_ids),
        )
        return new_plan_doc, merger_result, new_version

    async def _get_plan(self, session_id: UUID, version: int) -> models.Plan:
        from sqlalchemy import select

        stmt = (
            select(models.Plan)
            .where(models.Plan.session_id == session_id)
            .where(models.Plan.version == version)
        )
        result = await self._db.execute(stmt)
        plan = result.scalar_one_or_none()
        if plan is None:
            raise ValueError("invalid_plan_version")
        return plan

    async def advance_to_acting(self, session_id: UUID) -> models.Session:
        s = await self.get(session_id)
        plan = await self.get_current_plan(session_id)
        nodes: list[dict] = list(plan.document.get("nodes", []))
        if not self._all_blocking_answered(nodes):
            raise ValueError("not all blocking decisions answered")
        new_phase = transition(
            Phase(s.phase), Phase.ACTING, session_id=str(session_id),
        )
        s.phase = new_phase.value
        await self._db.flush()
        _log.info("session_advanced", session_id=str(session_id), new_phase=s.phase)
        return s
