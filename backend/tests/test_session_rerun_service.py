"""M4-26 SessionService.request_rerun 服务层测试（真 DB + rollback 隔离）。"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.plan_schemas import HeadingNode, PlanDocument, StepNode
from app.core.state_machine import InvalidTransitionError
from app.db import models
from app.services.session_service import SessionNotFoundError, SessionService

_PLAN = PlanDocument(
    title="T",
    summary="S",
    nodes=[
        HeadingNode(level=1, text="H1"),
        StepNode(title="a", description="da", tool="shell"),
        StepNode(id="step_x", title="b", description="db", tool="shell"),
        StepNode(title="c", description="dc", tool="shell"),
    ],
)

_LAST_RUN: dict[str, Any] = {
    "plan_version": 1,
    "all_ok": True,
    "steps": [
        {"step_id": "step_000", "ok": True, "git_commit": "a" * 40},
        {"step_id": "step_x", "ok": True, "git_commit": "b" * 40},
        {"step_id": "step_002", "ok": False, "git_commit": None},
    ],
}


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """每用例独立 engine + 真 AsyncSession，结束时 rollback 清掉写入。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_RERUN_QUERY_KEY", "sk-test")


async def _seed(
    db: AsyncSession,
    *,
    phase: str = "action_review",
    last_run: dict[str, Any] | None = _LAST_RUN,
) -> models.Session:
    adapter = models.ModelAdapter(
        name="t-rerun-adapter",
        provider="openai",
        model="gpt-4o",
        credentials_env={"api_key": "TEST_RERUN_QUERY_KEY"},
        params={},
    )
    db.add(adapter)
    await db.flush()

    svc = SessionService(db)
    session = await svc.create(init_request="r", adapter_id=adapter.id)
    await svc.save_plan(session_id=session.id, plan=_PLAN)
    session.phase = phase
    if last_run is not None:
        session.metadata_json = {"last_run": last_run}
    await db.flush()
    return session


# ===== R1: 成功登记 =====


async def test_rerun_ok_writes_pending_and_flips_phase(db: AsyncSession) -> None:
    session = await _seed(db)

    s = await SessionService(db).request_rerun(
        session_id=session.id, step_id="step_x",
    )

    assert s.phase == "acting"
    assert s.metadata_json["pending_rerun_from"] == "step_x"
    assert s.metadata_json["last_run"] == _LAST_RUN


async def test_rerun_accepts_fallback_step_id(db: AsyncSession) -> None:
    session = await _seed(db)

    s = await SessionService(db).request_rerun(
        session_id=session.id, step_id="step_002",
    )

    assert s.metadata_json["pending_rerun_from"] == "step_002"


# ===== R2: D4 校验矩阵 =====


async def test_rerun_session_not_found(db: AsyncSession) -> None:
    with pytest.raises(SessionNotFoundError):
        await SessionService(db).request_rerun(session_id=uuid4(), step_id="step_x")


async def test_rerun_illegal_phase(db: AsyncSession) -> None:
    session = await _seed(db, phase="planning")

    with pytest.raises(InvalidTransitionError):
        await SessionService(db).request_rerun(session_id=session.id, step_id="step_x")

    await db.refresh(session)
    assert "pending_rerun_from" not in (session.metadata_json or {})


async def test_rerun_no_run(db: AsyncSession) -> None:
    session = await _seed(db, last_run=None)

    with pytest.raises(ValueError, match="no_run"):
        await SessionService(db).request_rerun(session_id=session.id, step_id="step_x")

    await db.refresh(session)
    assert session.phase == "action_review"


async def test_rerun_garbage_last_run(db: AsyncSession) -> None:
    session = await _seed(db, last_run={"not": "a run"})

    with pytest.raises(ValueError, match="no_run"):
        await SessionService(db).request_rerun(session_id=session.id, step_id="step_x")


@pytest.mark.parametrize("step_id", ["step_009", "", "step_x ", "heading_000"])
async def test_rerun_step_not_in_plan(db: AsyncSession, step_id: str) -> None:
    session = await _seed(db)

    with pytest.raises(ValueError, match="step_not_found"):
        await SessionService(db).request_rerun(session_id=session.id, step_id=step_id)

    await db.refresh(session)
    assert session.phase == "action_review"


async def test_rerun_step_not_executed(db: AsyncSession) -> None:
    session = await _seed(db, last_run={
        "plan_version": 1,
        "all_ok": False,
        "steps": [{"step_id": "step_000", "ok": False, "git_commit": None}],
    })

    with pytest.raises(ValueError, match="step_not_executed"):
        await SessionService(db).request_rerun(session_id=session.id, step_id="step_x")

    await db.refresh(session)
    assert session.phase == "action_review"
    assert "pending_rerun_from" not in (session.metadata_json or {})


# ===== R3: 失败后重试（D5 回退路径的服务层前提）=====


async def test_rerun_retry_after_failure_rollback(db: AsyncSession) -> None:
    session = await _seed(db, last_run=None)
    with pytest.raises(ValueError, match="no_run"):
        await SessionService(db).request_rerun(session_id=session.id, step_id="step_x")

    session.metadata_json = {"last_run": _LAST_RUN}
    await db.flush()

    s = await SessionService(db).request_rerun(session_id=session.id, step_id="step_x")

    assert s.phase == "acting"
    assert s.metadata_json["pending_rerun_from"] == "step_x"
