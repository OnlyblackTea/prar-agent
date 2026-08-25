"""list_plans / get_plan service 测试：real AsyncSession（需要真 DB）。"""

from uuid import uuid4

import pytest

from app.core.plan_schemas import PlanDocument
from app.db import models
from app.services.session_service import SessionNotFoundError, SessionService

_PLAN_V1 = PlanDocument(
    title="T",
    summary="S",
    nodes=[
        {"type": "heading", "level": 1, "text": "H1"},
        {"type": "paragraph", "text": "original text"},
    ],
)


@pytest.fixture
async def db():
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
def _fake_credentials(monkeypatch):
    monkeypatch.setenv("TEST_PLAN_QUERY_KEY", "sk-test")


async def _seed(db, *, save_plan: bool = True, extra_versions: int = 0):
    """造 adapter + session（+ v1 plan + 追加 extra_versions 个手工版本行）。"""
    adapter = models.ModelAdapter(
        name="t-adapter",
        provider="openai",
        model="gpt-4o",
        credentials_env={"api_key": "TEST_PLAN_QUERY_KEY"},
        params={},
    )
    db.add(adapter)
    await db.flush()

    svc = SessionService(db)
    session = await svc.create(init_request="r", adapter_id=adapter.id)
    if save_plan:
        await svc.save_plan(session_id=session.id, plan=_PLAN_V1)
        for i in range(extra_versions):
            db.add(
                models.Plan(
                    session_id=session.id,
                    version=2 + i,
                    document=_PLAN_V1.model_dump(),
                )
            )
            session.current_plan_version = 2 + i
        await db.flush()
    return session


async def test_list_plans_ascending(db):
    """case 1: 多版本按 version 升序返回，附带 session。"""
    session = await _seed(db, extra_versions=2)
    svc = SessionService(db)
    s, plans = await svc.list_plans(session.id)

    assert s.id == session.id
    assert [p.version for p in plans] == [1, 2, 3]
    assert s.current_plan_version == 3


async def test_list_plans_empty_when_no_plan(db):
    """case 2: session 无 plan → 空列表（不是 404）。"""
    session = await _seed(db, save_plan=False)
    svc = SessionService(db)
    _, plans = await svc.list_plans(session.id)
    assert plans == []


async def test_list_plans_session_not_found(db):
    """case 3: session 不存在 → SessionNotFoundError。"""
    svc = SessionService(db)
    with pytest.raises(SessionNotFoundError):
        await svc.list_plans(uuid4())


async def test_get_plan_returns_requested_version(db):
    """case 4: 指定版本命中，document 完整。"""
    session = await _seed(db, extra_versions=1)
    svc = SessionService(db)
    plan = await svc.get_plan(session.id, 2)
    assert plan.version == 2
    assert plan.document["title"] == "T"


async def test_get_plan_invalid_version(db):
    """case 5: 越界版本 → ValueError('plan_version_not_found')。"""
    session = await _seed(db)
    svc = SessionService(db)
    with pytest.raises(ValueError, match="plan_version_not_found"):
        await svc.get_plan(session.id, 99)


async def test_get_plan_session_not_found(db):
    """case 6: session 不存在 → SessionNotFoundError（优先于版本校验）。"""
    svc = SessionService(db)
    with pytest.raises(SessionNotFoundError):
        await svc.get_plan(uuid4(), 1)
