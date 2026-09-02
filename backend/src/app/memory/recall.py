"""LTM 三层记忆检索 — 余弦 top-k 后加权排序，注入 planner 的 {ltm_recall} 段落。"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from app.config import Settings, get_settings
from app.core.logging import get_logger
from app.services.memory_service import MemoryHit, MemoryService

_log = get_logger("ltm_recall")

RECALL_KINDS = ("episodic", "semantic", "procedural")
CANDIDATE_LIMIT = 20


@dataclass(frozen=True, slots=True)
class RankedHit:
    kind: str
    content: str
    cosine: float
    importance: float
    final: float


def _recency_factor(age_seconds: float, half_life_days: float) -> float:
    """0.5^(age/半衰期)：30 天前 → 0.5；刚产生 → 1.0。"""
    if age_seconds <= 0:
        return 1.0
    return math.pow(0.5, age_seconds / 86400.0 / half_life_days)


def _rank(
    hits: Sequence[MemoryHit],
    *,
    now: datetime,
    min_score: float,
    half_life_days: float,
) -> list[RankedHit]:
    ranked: list[RankedHit] = []
    for hit in hits:
        if hit.score < min_score:
            continue
        age = (now - hit.memory.created_at).total_seconds()
        recency = _recency_factor(age, half_life_days)
        ranked.append(
            RankedHit(
                kind=hit.memory.kind,
                content=hit.memory.content,
                cosine=hit.score,
                importance=hit.memory.importance,
                final=hit.score * hit.memory.importance * recency,
            )
        )
    ranked.sort(key=lambda r: r.final, reverse=True)
    return ranked


def _format(ranked: Sequence[RankedHit]) -> list[str]:
    return [
        f"[{r.kind}|{r.cosine:.2f}|{r.importance:.2f}] {r.content}"
        for r in ranked
    ]


class LtmRecall:
    """query → 三层记忆检索 → 加权 top_n 注入行；store 绑定 DB + embedding。"""

    def __init__(
        self, store: MemoryService, settings: Settings | None = None,
    ) -> None:
        self._store = store
        self._settings = settings or get_settings()

    async def recall(self, *, query: str) -> list[str]:
        s = self._settings
        hits = await self._store.search(
            query=query, limit=CANDIDATE_LIMIT, kinds=RECALL_KINDS,
        )
        ranked = _rank(
            hits,
            now=datetime.now(UTC),
            min_score=s.ltm_recall_min_score,
            half_life_days=s.ltm_recall_half_life_days,
        )
        top = ranked[: s.ltm_recall_top_n]
        _log.info(
            "ltm_recall",
            query=query[:60],
            candidates=len(hits),
            injected=len(top),
            scores=[round(r.final, 4) for r in top],
        )
        return _format(top)
