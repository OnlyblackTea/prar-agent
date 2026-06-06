"""Session 服务层：创建 session、管理 plan、答题、推进阶段。"""

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.plan_schemas import PlanDocument
from app.core.state_machine import Phase, transition
from app.db import models

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
