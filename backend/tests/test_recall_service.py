"""M4-25 LtmRecall 服务层测试 — 真 DB（pgvector）+ fake embedding，rollback 隔离。"""

import hashlib
from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.memory.recall import LtmRecall
from app.services.memory_service import MemoryService

DIM = 1536


class _FakeEmbedding:
    """确定性伪嵌入：同文本同向量（sha256 派生），不同文本在 1536 维下近似正交。"""

    async def embed_one(self, text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [((digest[i % 32] / 255.0) * 2.0 - 1.0) for i in range(DIM)]


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


def _svc(db: AsyncSession) -> MemoryService:
    return MemoryService(db, _FakeEmbedding())


# ===== S1: 命中间查询语义的行 + access 记账 =====


async def test_recall_hits_matching_row_and_accounts(db: AsyncSession) -> None:
    svc = _svc(db)
    target = await svc.store(
        kind="semantic", content="用户偏好 React 技术栈", importance=0.9,
    )
    await svc.store(kind="episodic", content="首次 session 讨论登录页", importance=0.5)

    lines = await LtmRecall(svc).recall(query="用户偏好 React 技术栈")

    assert len(lines) == 1
    assert lines[0].startswith("[semantic|")
    assert "用户偏好 React 技术栈" in lines[0]

    await db.refresh(target)
    assert target.access_count == 1


# ===== S2: 空库 → 空注入 =====


async def test_recall_empty_db_returns_empty(db: AsyncSession) -> None:
    lines = await LtmRecall(_svc(db)).recall(query="任意需求")
    assert lines == []


# ===== S3: 多种类同行内容命中 + importance 加权排序 =====


async def test_recall_multi_kind_orders_by_importance(db: AsyncSession) -> None:
    svc = _svc(db)
    await svc.store(kind="episodic", content="项目要用 Docker 部署", importance=0.5)
    await svc.store(kind="semantic", content="项目要用 Docker 部署", importance=0.9)

    lines = await LtmRecall(svc).recall(query="项目要用 Docker 部署")

    assert len(lines) == 2
    assert lines[0].startswith("[semantic|")
    assert lines[1].startswith("[episodic|")


# ===== S4: 正交向量被余弦阈值过滤（真实 pgvector 余弦）=====


async def test_recall_filters_orthogonal_rows(db: AsyncSession) -> None:
    svc = _svc(db)
    await svc.store(kind="semantic", content="目标记忆内容", importance=0.8)
    await svc.store(kind="semantic", content="完全不相关的另一段记忆", importance=0.8)
    await svc.store(kind="episodic", content="还有一条无关的 episodic", importance=0.8)

    lines = await LtmRecall(svc).recall(query="目标记忆内容")

    assert len(lines) == 1
    assert "目标记忆内容" in lines[0]


# ===== S5: recency 权重压过 importance（新鲜低 importance 排前）=====


async def test_recall_fresh_low_importance_beats_stale_high(db: AsyncSession) -> None:
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import update

    svc = _svc(db)
    await svc.store(
        kind="semantic", content="历史决策 X", importance=0.9,
    )  # 新鲜：final = 1.0 × 0.9 × 1.0 = 0.9
    stale = await svc.store(
        kind="semantic", content="历史决策 X", importance=1.0,
    )  # 陈旧 60 天：final = 1.0 × 1.0 × 0.25 = 0.25
    await db.execute(
        update(models.Memory)
        .where(models.Memory.id == stale.id)
        .values(created_at=datetime.now(UTC) - timedelta(days=60))
    )

    lines = await LtmRecall(svc).recall(query="历史决策 X")

    assert len(lines) == 2
    # 两条同内容同余弦，用 importance 标签区分：低 importance 的新鲜行必须排第一
    assert "|0.90]" in lines[0]
    assert "|1.00]" in lines[1]
