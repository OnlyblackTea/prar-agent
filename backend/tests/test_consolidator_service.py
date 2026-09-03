"""M4-24 Consolidator 服务层测试 — 真 DB（pgvector）+ fake LLM/fake embedding，rollback 隔离。"""

import hashlib
from collections.abc import AsyncIterator
from typing import Any, NamedTuple
from uuid import UUID

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import models
from app.llm.router import LLMTransportError
from app.services.memory_service import MemoryService

DIM = 1536


class _FakeEmbedding:
    """确定性伪嵌入：同文本同向量（sha256 派生），不同文本在 1536 维下近似正交。"""

    def __init__(self) -> None:
        self.calls = 0

    async def embed_one(self, text: str) -> list[float]:
        self.calls += 1
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        return [((digest[i % 32] / 255.0) * 2.0 - 1.0) for i in range(DIM)]


class _FakeRouter:
    """LLMRouter 替身：返回预设提炼结果（Consolidator 自己的 schema）。"""

    def __init__(
        self,
        items: list[Any] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.items = items if items is not None else []
        self.error = error
        self.calls = 0

    async def complete_structured(self, **kwargs: Any) -> Any:
        from app.llm.router import StructuredResponse, TokenUsage
        from app.memory.consolidator import ConsolidatedMemories

        self.calls += 1
        if self.error is not None:
            raise self.error
        parsed = ConsolidatedMemories(items=list(self.items))
        return StructuredResponse[ConsolidatedMemories](
            parsed=parsed,
            raw_text=parsed.model_dump_json(),
            model_id="fake-model",
            usage=TokenUsage(),
            finish_reason="stop",
        )


def make_item(kind: str, content: str, importance: float = 0.5) -> Any:
    from app.memory.consolidator import DistilledMemory

    return DistilledMemory(kind=kind, content=content, importance=importance)


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
    monkeypatch.setenv("CONSOLIDATOR_TEST_KEY", "sk-test")


async def _clear_defaults(db: AsyncSession) -> None:
    """VM DB 可能残留真实默认 adapter，测试事务内先清空保证确定性（rollback 复原）。"""
    await db.execute(
        update(models.ModelAdapter)
        .where(models.ModelAdapter.is_default.is_(True))
        .values(is_default=False)
    )


class _Baseline(NamedTuple):
    decayable: int


async def _isolate(db: AsyncSession) -> _Baseline:
    """中和共享 VM 库残留行，令全表扫描型断言确定化（事务内执行，teardown rollback 复原）。

    与 _clear_defaults 同一手法。必须在 _seed_memory 之前调用，否则会消费掉本用例自己的原料。
    """
    await db.execute(
        update(models.Memory)
        .where(
            models.Memory.kind == "episodic",
            models.Memory.consolidated_at.is_(None),
        )
        .values(consolidated_at=func.now())
    )
    decayable = (
        await db.execute(
            select(func.count())
            .select_from(models.Memory)
            .where(models.Memory.kind.in_(("semantic", "procedural")))
        )
    ).scalar_one()
    return _Baseline(decayable=int(decayable))


async def _seed_adapter(db: AsyncSession) -> models.ModelAdapter:
    await _clear_defaults(db)
    adapter = models.ModelAdapter(
        name="consolidator-test",
        provider="openai",
        model="gpt-4o",
        credentials_env={"api_key": "CONSOLIDATOR_TEST_KEY"},
        params={},
        is_default=True,
    )
    db.add(adapter)
    await db.flush()
    return adapter


async def _seed_memory(
    db: AsyncSession,
    emb: _FakeEmbedding,
    *,
    kind: str,
    content: str,
    importance: float = 0.5,
) -> models.Memory:
    return await MemoryService(db, emb).store(
        kind=kind, content=content, importance=importance,
    )


async def _run(
    db: AsyncSession, router: _FakeRouter, emb: _FakeEmbedding,
) -> Any:
    """解析默认 adapter → run_once（仅 flush，不 commit —— 保持 rollback 隔离）。"""
    from app.memory.consolidator import Consolidator, resolve_default_adapter

    adapter = await resolve_default_adapter(db)
    c = Consolidator(
        db=db,
        store=MemoryService(db, emb),
        router=router,  # type: ignore[arg-type]
        embedding=emb,
    )
    return await c.run_once(adapter=adapter)


async def _semantic_ids(db: AsyncSession) -> set[UUID]:
    rows = await db.execute(
        select(models.Memory.id).where(models.Memory.kind == "semantic")
    )
    return set(rows.scalars().all())


async def _pending_episodic_ids(db: AsyncSession, emb: _FakeEmbedding) -> set[UUID]:
    """走产品的 list_unconsolidated —— 断言真批次入口，而非本文件抄一份等价 SQL。"""
    # 伪嵌入替身不是 EmbeddingService 子类；同 _run 里对 router 的处理方式
    store = MemoryService(db, emb)  # type: ignore[arg-type]
    return {m.id for m in await store.list_unconsolidated(limit=20)}


# ===== C1: 提炼 + 插入 + 标记 =====


async def test_distill_inserts_and_marks_batch(db: AsyncSession) -> None:
    await _seed_adapter(db)
    emb = _FakeEmbedding()
    await _isolate(db)
    rows = [
        await _seed_memory(db, emb, kind="episodic", content=f"需求：{i}")
        for i in range(3)
    ]
    router = _FakeRouter(items=[
        make_item("semantic", "跨会话知识 k1", 0.6),
        make_item("procedural", "可复用方法 k2", 0.7),
    ])

    result = await _run(db, router, emb)

    assert result.processed == 3
    assert result.distilled == 2
    assert result.inserted == 2
    assert result.merged == 0
    assert router.calls == 1

    new_rows = (
        (
            await db.execute(
                select(models.Memory).where(models.Memory.kind != "episodic")
            )
        )
        .scalars()
        .all()
    )
    assert {m.kind for m in new_rows} == {"semantic", "procedural"}

    for r in rows:
        await db.refresh(r)
        assert r.consolidated_at is not None


# ===== C2: 幂等 — 第二轮零 LLM =====


async def test_second_run_no_llm_and_no_new_rows(db: AsyncSession) -> None:
    await _seed_adapter(db)
    emb = _FakeEmbedding()
    base = await _isolate(db)
    await _seed_memory(db, emb, kind="episodic", content="需求：A")
    router = _FakeRouter(items=[make_item("semantic", "知识 k1", 0.6)])

    first = await _run(db, router, emb)
    assert first.processed == 1

    second = await _run(db, router, emb)

    assert router.calls == 1
    assert second.processed == 0
    assert second.distilled == 0
    assert second.inserted == 0
    assert second.merged == 0
    # 上一轮插入的 1 条 semantic 照常衰减；残留行计入基线
    assert second.decayed == base.decayable + 1


# ===== C3: 衰减 factor/floor，episodic 不衰减 =====


async def test_decay_factor_floor_and_episodic_untouched(db: AsyncSession) -> None:
    await _seed_adapter(db)
    emb = _FakeEmbedding()
    base = await _isolate(db)
    hot = await _seed_memory(db, emb, kind="semantic", content="热门", importance=1.0)
    cold = await _seed_memory(db, emb, kind="semantic", content="冷门", importance=0.05)
    ep = await _seed_memory(db, emb, kind="episodic", content="ep1", importance=0.9)

    result = await _run(db, _FakeRouter(items=[]), emb)

    await db.refresh(hot)
    await db.refresh(cold)
    await db.refresh(ep)
    assert hot.importance == pytest.approx(0.9)
    assert cold.importance == pytest.approx(0.1)
    assert ep.importance == pytest.approx(0.9)
    assert result.decayed == base.decayable + 2
    assert result.processed == 1  # 空提炼也消费了这批原料
    assert ep.consolidated_at is not None


# ===== C4: 合并路径（真实 pgvector 余弦）=====


async def test_merge_when_identical_vector(db: AsyncSession) -> None:
    await _seed_adapter(db)
    emb = _FakeEmbedding()
    await _isolate(db)
    existing = await _seed_memory(
        db, emb, kind="semantic", content="知识 K", importance=0.9,
    )
    await _seed_memory(db, emb, kind="episodic", content="需求：x")
    router = _FakeRouter(items=[make_item("semantic", "知识 K", 0.3)])

    before = await _semantic_ids(db)
    result = await _run(db, router, emb)

    assert result.inserted == 0
    assert result.merged == 1

    # 合并走 UPDATE 覆盖既有行，不新增行 —— id 集合不变（残留行同在两侧，不影响差分）
    assert await _semantic_ids(db) == before
    await db.refresh(existing)
    assert existing.content == "知识 K"
    # importance = max(0.9, 0.3) = 0.9，随后本轮衰减 → 0.81
    assert existing.importance == pytest.approx(0.81)


# ===== C5: 无默认 adapter =====


async def test_no_default_adapter_raises(db: AsyncSession) -> None:
    from app.memory.consolidator import NoDefaultAdapterError, resolve_default_adapter

    await _clear_defaults(db)
    emb = _FakeEmbedding()
    await _seed_memory(db, emb, kind="episodic", content="需求：x")

    assert await resolve_default_adapter(db) is None
    from app.memory.consolidator import Consolidator

    c = Consolidator(
        db=db,
        store=MemoryService(db, emb),
        router=_FakeRouter(),  # type: ignore[arg-type]
        embedding=emb,
    )
    with pytest.raises(NoDefaultAdapterError):
        await c.run_once(adapter=None)


# ===== C6: LLM 失败 → 批次保持未消费 =====


async def test_llm_error_keeps_batch_unconsumed(db: AsyncSession) -> None:
    from app.memory.consolidator import Consolidator, resolve_default_adapter

    await _seed_adapter(db)
    emb = _FakeEmbedding()
    await _isolate(db)
    row = await _seed_memory(db, emb, kind="episodic", content="需求：x")
    router = _FakeRouter(error=LLMTransportError("boom", model_id="m"))

    c = Consolidator(
        db=db,
        store=MemoryService(db, emb),
        router=router,  # type: ignore[arg-type]
        embedding=emb,
    )
    adapter = await resolve_default_adapter(db)
    with pytest.raises(LLMTransportError):
        await c.run_once(adapter=adapter)

    # 未 commit 的会话内状态即可验证：LLM 报错前批次尚未 mark_consolidated
    await db.refresh(row)
    assert row.consolidated_at is None


# ===== C7: _isolate 中和共享库残留（24.1 回归）=====


async def test_isolate_neutralizes_shared_db_residue(db: AsyncSession) -> None:
    """别人已 commit 的残留行必须被中和，否则全表扫描型断言只是"库恰好为空"时才成立。"""
    emb = _FakeEmbedding()
    clean = await _isolate(db)  # 先中和真实残留，取得干净基线

    stray_ep = await _seed_memory(
        db, emb, kind="episodic", content="残留：上轮 E2E 未消费",
    )
    await _seed_memory(db, emb, kind="semantic", content="残留：上轮提炼结果")
    assert stray_ep.id in await _pending_episodic_ids(db, emb)

    base = await _isolate(db)

    assert await _pending_episodic_ids(db, emb) == set()
    assert base.decayable == clean.decayable + 1
    await db.refresh(stray_ep)  # refresh 不抛 → 行仍在：中和走 UPDATE 标记，非 DELETE
    assert stray_ep.consolidated_at is not None
