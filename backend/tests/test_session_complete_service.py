"""M4-23 SessionService.complete 服务层测试（真 DB + fake LongTermMemory，rollback 隔离）。"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.embedding import EmbeddingTransportError
from app.core.plan_schemas import HeadingNode, ParagraphNode, PlanDocument
from app.core.state_machine import InvalidTransitionError
from app.db import models
from app.services.session_service import SessionNotFoundError, SessionService

_PLAN_V1 = PlanDocument(
    title="T",
    summary="S",
    nodes=[
        HeadingNode(level=1, text="H1"),
        ParagraphNode(text="original text"),
    ],
)


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    """每用例独立 engine + 真 AsyncSession，结束时 rollback 清掉写入。"""
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import get_settings

    engine = create_async_engine(
        get_settings().database_url, pool_pre_ping=True,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as session:
            yield session
            await session.rollback()
    finally:
        await engine.dispose()


@pytest.fixture(autouse=True)
def _fake_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TEST_COMPLETE_QUERY_KEY", "sk-test")


async def _seed(
    db: AsyncSession,
    *,
    phase: str = "action_review",
    save_plan: bool = True,
    last_run: dict[str, Any] | None = None,
) -> models.Session:
    adapter = models.ModelAdapter(
        name="t-adapter",
        provider="openai",
        model="gpt-4o",
        credentials_env={"api_key": "TEST_COMPLETE_QUERY_KEY"},
        params={},
    )
    db.add(adapter)
    await db.flush()

    svc = SessionService(db)
    session = await svc.create(init_request="r", adapter_id=adapter.id)
    if save_plan:
        await svc.save_plan(session_id=session.id, plan=_PLAN_V1)
    session.phase = phase
    if last_run is not None:
        session.metadata_json = {"last_run": last_run}
    await db.flush()
    return session


class _FakeLongTerm:
    """LongTermMemory 替身：记录调用参数，可注入错误。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.error: Exception | None = None

    async def record_episodic(self, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.calls.append(kwargs)


# ===== C1: 正常完成 =====


async def test_complete_success(db: AsyncSession) -> None:
    session = await _seed(db)
    fake = _FakeLongTerm()
    svc = SessionService(db)

    s = await svc.complete(session_id=session.id, long_term=fake)

    assert s.phase == "done"
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["session_id"] == session.id
    assert call["init_request"] == "r"
    assert call["plan_version"] == 1
    assert call["plan"].title == "T"
    assert call["run"] is None


# ===== C2: last_run 解析 =====


async def test_complete_parses_last_run(db: AsyncSession) -> None:
    session = await _seed(
        db,
        last_run={
            "plan_version": 1,
            "all_ok": True,
            "steps": [{"step_id": "step_000", "ok": True, "git_commit": "abc"}],
        },
    )
    fake = _FakeLongTerm()

    await SessionService(db).complete(session_id=session.id, long_term=fake)

    run = fake.calls[0]["run"]
    assert run is not None
    assert run.plan_version == 1
    assert run.all_ok is True
    assert run.steps[0].step_id == "step_000"
    assert run.steps[0].ok is True
    assert run.steps[0].git_commit == "abc"


async def test_complete_tolerates_garbage_last_run(db: AsyncSession) -> None:
    session = await _seed(db, last_run={"not": "a valid summary"})
    fake = _FakeLongTerm()

    await SessionService(db).complete(session_id=session.id, long_term=fake)

    assert fake.calls[0]["run"] is None


# ===== C3/C4: 非法 phase =====


async def test_complete_illegal_phase_acting(db: AsyncSession) -> None:
    session = await _seed(db, phase="acting")
    fake = _FakeLongTerm()

    with pytest.raises(InvalidTransitionError):
        await SessionService(db).complete(session_id=session.id, long_term=fake)

    assert fake.calls == []


async def test_complete_twice_rejected(db: AsyncSession) -> None:
    session = await _seed(db, phase="done")
    fake = _FakeLongTerm()

    with pytest.raises(InvalidTransitionError):
        await SessionService(db).complete(session_id=session.id, long_term=fake)

    assert fake.calls == []


# ===== C5: session 不存在 =====


async def test_complete_session_not_found(db: AsyncSession) -> None:
    with pytest.raises(SessionNotFoundError):
        await SessionService(db).complete(
            session_id=uuid4(), long_term=_FakeLongTerm(),
        )


# ===== C6: embedding 失败不转 done =====


async def test_complete_embedding_error_no_transition(db: AsyncSession) -> None:
    session = await _seed(db)
    fake = _FakeLongTerm()
    fake.error = EmbeddingTransportError("boom", model_id="m")

    with pytest.raises(EmbeddingTransportError):
        await SessionService(db).complete(session_id=session.id, long_term=fake)

    await db.refresh(session)
    assert session.phase == "action_review"
