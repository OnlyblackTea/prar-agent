"""merge_plan 集成测试：real AsyncSession + mock router（需要真 DB）。"""

from collections.abc import AsyncIterator
from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.merger_schemas import MergerAction, MergerResult
from app.core.plan_schemas import (
    CriticAction,
    HeadingNode,
    ParagraphNode,
    PlanDocument,
)
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
    """每用例独立 engine + 真 AsyncSession，结束时 rollback 清掉写入。

    不复用全局 engine：pytest-asyncio 每用例新 loop，连接池跨 loop 会报
    "Event loop is closed"。
    """
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
    monkeypatch.setenv("TEST_MERGE_KEY", "sk-test")


def _mock_router(result: MergerResult) -> AsyncMock:
    router = AsyncMock()
    resp = MagicMock()
    resp.parsed = result
    router.complete_structured.return_value = resp
    return router


def _patch_replace(index: int, text: str) -> CriticAction:
    return CriticAction(
        node_index=index,
        action="replace",
        reason="test",
        replacement=ParagraphNode(text=text),
    )


async def _seed(
    db: AsyncSession, *, phase: str = "plan_review", n_comments: int = 0
) -> tuple[models.Session, list[models.Comment]]:
    """造 adapter + session(v1 plan, plan_review) + n 条 unresolved 评论。"""
    adapter = models.ModelAdapter(
        name="t-adapter",
        provider="openai",
        model="gpt-4o",
        credentials_env={"api_key": "TEST_MERGE_KEY"},
        params={},
    )
    db.add(adapter)
    await db.flush()

    svc = SessionService(db)
    session = await svc.create(init_request="r", adapter_id=adapter.id)
    await svc.save_plan(session_id=session.id, plan=_PLAN_V1)
    if phase != "plan_review":
        session.phase = phase
        await db.flush()

    comments = []
    for i in range(n_comments):
        c = models.Comment(
            session_id=session.id,
            plan_version=1,
            anchor_id=f"p-{i}",
            quote="original text",
            quote_context="",
            body=f"comment {i}",
        )
        db.add(c)
        comments.append(c)
    await db.flush()
    return session, comments


async def _plan_versions(db: AsyncSession, session_id: UUID) -> int:
    result = await db.execute(
        select(models.Plan).where(models.Plan.session_id == session_id)
    )
    return len(list(result.scalars().all()))


async def test_merge_normal_creates_v2_and_marks_accepted(
    db: AsyncSession,
) -> None:
    """case 1: accept + reject 混合 → v2 落库，仅 accepted 标 resolved。"""
    session, (c1, c2) = await _seed(db, n_comments=2)
    result = MergerResult(
        actions=[
            MergerAction(
                comment_id=c1.id,
                decision="accept",
                reason="ok",
                patch=_patch_replace(1, "revised text"),
            ),
            MergerAction(comment_id=c2.id, decision="reject", reason="no"),
        ],
        overall_comment="done",
    )
    svc = SessionService(db)
    new_plan, merger_result, version = await svc.merge_plan(
        session_id=session.id, router=_mock_router(result),
    )

    assert version == 2
    assert session.current_plan_version == 2
    assert await _plan_versions(db, session.id) == 2
    assert cast(ParagraphNode, new_plan.nodes[1]).text == "revised text"
    assert len(merger_result.actions) == 2
    assert c1.resolved is True
    assert c2.resolved is False
    assert session.phase == "plan_review"  # merge 不动 phase


async def test_merge_no_unresolved_comments_raises(db: AsyncSession) -> None:
    """case 2: 无 unresolved comments → ValueError。"""
    session, _ = await _seed(db, n_comments=0)
    svc = SessionService(db)
    with pytest.raises(ValueError, match="no_comments_to_merge"):
        await svc.merge_plan(session_id=session.id, router=_mock_router(MergerResult()))


async def test_merge_wrong_phase_raises(db: AsyncSession) -> None:
    """case 3: phase=acting → ValueError。"""
    session, _ = await _seed(db, phase="acting", n_comments=1)
    svc = SessionService(db)
    with pytest.raises(ValueError, match="phase_not_review"):
        await svc.merge_plan(session_id=session.id, router=_mock_router(MergerResult()))


async def test_merge_all_reject_keeps_version(db: AsyncSession) -> None:
    """case 4: 全 reject → 不落新版本，comments 保持 unresolved。"""
    session, (c1,) = await _seed(db, n_comments=1)
    result = MergerResult(
        actions=[
            MergerAction(comment_id=c1.id, decision="reject", reason="no"),
        ],
    )
    svc = SessionService(db)
    plan, merger_result, version = await svc.merge_plan(
        session_id=session.id, router=_mock_router(result),
    )

    assert version == 1
    assert session.current_plan_version == 1
    assert await _plan_versions(db, session.id) == 1
    assert cast(ParagraphNode, plan.nodes[1]).text == "original text"
    assert len(merger_result.actions) == 1
    assert c1.resolved is False


async def test_merge_session_not_found(db: AsyncSession) -> None:
    """case 5: session 不存在 → SessionNotFoundError。"""
    svc = SessionService(db)
    with pytest.raises(SessionNotFoundError):
        await svc.merge_plan(session_id=uuid4(), router=_mock_router(MergerResult()))


async def test_merge_fabricated_comment_id_is_noop(db: AsyncSession) -> None:
    """case 6: LLM 编造不存在的 comment_id → mark_resolved 安静 no-op。"""
    session, (c1,) = await _seed(db, n_comments=1)
    result = MergerResult(
        actions=[
            MergerAction(
                comment_id=c1.id,
                decision="accept",
                reason="ok",
                patch=_patch_replace(1, "revised text"),
            ),
            MergerAction(
                comment_id=uuid4(),  # 编造的 id
                decision="accept",
                reason="fabricated",
            ),
        ],
    )
    svc = SessionService(db)
    _, _, version = await svc.merge_plan(
        session_id=session.id, router=_mock_router(result),
    )

    assert version == 2
    assert c1.resolved is True


async def test_merge_from_action_review_goes_to_plan_review(
    db: AsyncSession,
) -> None:
    """M4-27 D4 MG1：action_review 改 plan → 两跳落 plan_review。"""
    session, (c1,) = await _seed(db, phase="action_review", n_comments=1)
    result = MergerResult(
        actions=[
            MergerAction(
                comment_id=c1.id,
                decision="accept",
                reason="ok",
                patch=_patch_replace(1, "revised text"),
            ),
        ],
        overall_comment="",
    )
    svc = SessionService(db)
    new_plan, _, version = await svc.merge_plan(
        session_id=session.id, router=_mock_router(result),
    )

    assert version == 2
    assert session.current_plan_version == 2
    assert session.phase == "plan_review"
    assert c1.resolved is True
    assert cast(ParagraphNode, new_plan.nodes[1]).text == "revised text"


async def test_merge_from_action_review_all_reject_keeps_phase(
    db: AsyncSession,
) -> None:
    """M4-27 D4 MG2：全 reject 时两跳不得触发，phase 与版本原地不动。"""
    session, (c1,) = await _seed(db, phase="action_review", n_comments=1)
    result = MergerResult(
        actions=[MergerAction(comment_id=c1.id, decision="reject", reason="no")],
    )
    svc = SessionService(db)
    plan, _, version = await svc.merge_plan(
        session_id=session.id, router=_mock_router(result),
    )

    assert version == 1
    assert session.current_plan_version == 1
    assert session.phase == "action_review"
    assert c1.resolved is False
    assert cast(ParagraphNode, plan.nodes[1]).text == "original text"
