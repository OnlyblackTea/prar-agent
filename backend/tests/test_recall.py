"""LTM recall 单元测试（全 mock：加权排序/阈值过滤/截断/格式化/空命中）。"""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.memory.recall import (
    CANDIDATE_LIMIT,
    RECALL_KINDS,
    LtmRecall,
    _format,
    _rank,
    _recency_factor,
)
from app.services.memory_service import MemoryHit

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


def _hit(
    content: str,
    *,
    kind: str = "semantic",
    score: float = 0.8,
    importance: float = 0.5,
    created_at: datetime = NOW,
) -> MemoryHit:
    mem = SimpleNamespace(
        kind=kind,
        content=content,
        importance=importance,
        created_at=created_at,
    )
    return MemoryHit(memory=mem, score=score)  # type: ignore[arg-type]


# ===== R1: recency 半衰期 =====


def test_recency_half_life_is_exact() -> None:
    assert _recency_factor(age_seconds=30 * 86400.0, half_life_days=30) == 0.5
    assert _recency_factor(age_seconds=0.0, half_life_days=30) == 1.0


# ===== R2: 加权排序 final = cosine × importance × recency =====


def test_rank_orders_by_final_score() -> None:
    fresh = _hit(
        "新鲜但低分", score=0.4, importance=0.9, created_at=NOW,
    )  # final = 0.4 * 0.9 * 1.0 = 0.36
    stale = _hit(
        "陈旧但高分",
        score=0.9,
        importance=0.5,
        created_at=NOW - timedelta(days=30),
    )  # final = 0.9 * 0.5 * 0.5 = 0.225

    ranked = _rank([stale, fresh], now=NOW, min_score=0.0, half_life_days=30)

    assert [r.content for r in ranked] == ["新鲜但低分", "陈旧但高分"]
    assert ranked[0].final == pytest.approx(0.36)
    assert ranked[1].final == pytest.approx(0.225)


# ===== R3: 余弦低于阈值被过滤 =====


def test_rank_filters_below_min_score() -> None:
    hits = [
        _hit("相关", score=0.6),
        _hit("无关", score=0.2),
    ]
    ranked = _rank(hits, now=NOW, min_score=0.35, half_life_days=30)
    assert [r.content for r in ranked] == ["相关"]


# ===== R4: 格式化 =====


def test_format_lines() -> None:
    ranked = _rank(
        [
            _hit("知识 K", kind="semantic", score=0.816, importance=0.9),
        ],
        now=NOW,
        min_score=0.0,
        half_life_days=30,
    )
    lines = _format(ranked)
    assert lines == ["[semantic|0.82|0.90] 知识 K"]


# ===== R5: 空命中 → 空注入 =====


async def test_recall_empty_hits_returns_empty() -> None:
    store = AsyncMock()
    store.search.return_value = []
    recall = LtmRecall(store)

    lines = await recall.recall(query="新需求")

    assert lines == []
    store.search.assert_awaited_once_with(
        query="新需求", limit=CANDIDATE_LIMIT, kinds=RECALL_KINDS,
    )


# ===== R6: top_n 截断 + search 参数透传 =====


async def test_recall_truncates_to_top_n() -> None:
    from app.config import Settings

    store = AsyncMock()
    store.search.return_value = [
        _hit(f"记忆{i}", score=0.9, importance=0.9) for i in range(8)
    ]
    settings = Settings(ltm_recall_top_n=3)
    recall = LtmRecall(store, settings=settings)

    lines = await recall.recall(query="需求")

    assert len(lines) == 3
    assert lines[0].startswith("[semantic|0.90|0.90] 记忆0")
