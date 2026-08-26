"""ReviewMerger 单元测试（全部 mock LLM，不真调 API）。"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

import pytest

from app.core.merger_schemas import MergerAction, MergerResult
from app.core.plan_schemas import (
    CriticAction,
    ParagraphNode,
    PlanDocument,
)
from app.core.review_merger import ReviewMerger, _comments_to_prompt_json
from app.db.models import Comment
from app.llm.types import ResolvedAdapter

# ===== Helpers =====


def _adapter() -> ResolvedAdapter:
    return ResolvedAdapter(
        id=uuid4(),
        name="test-adapter",
        provider="openai",
        model="gpt-4",
        credentials={"api_key": "sk-test"},
        params={},
    )


def _mock_response(parsed: MergerResult) -> MagicMock:
    """构造一个 mock 的 StructuredResponse，只暴露 .parsed 属性。"""
    mock = MagicMock()
    mock.parsed = parsed
    return mock


def _plan() -> PlanDocument:
    return PlanDocument(
        title="T",
        summary="S",
        nodes=[
            ParagraphNode(text="first paragraph"),
            ParagraphNode(text="second paragraph"),
        ],
    )


def _comment_row(comment_id: UUID | None = None) -> MagicMock:
    """MagicMock ORM 行（同 test_comment_service.py 风格）。"""
    c = MagicMock(spec=Comment)
    c.id = comment_id or uuid4()
    c.anchor_id = "anc_001"
    c.quote = "first paragraph"
    c.quote_context = "ctx"
    c.body = "please revise"
    return c


@pytest.fixture
def mock_router() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def merger(mock_router: AsyncMock) -> ReviewMerger:
    return ReviewMerger(mock_router)


# ===== Case 1: comments 空 → 不调 LLM 直接返 =====


async def test_merge_empty_comments_skips_llm(
    merger: ReviewMerger, mock_router: AsyncMock
) -> None:
    plan = _plan()
    new_plan, result = await merger.merge(
        plan=plan, comments=[], adapter=_adapter(),
    )
    assert new_plan is plan
    assert result.actions == []
    mock_router.complete_structured.assert_not_called()


# ===== Case 2: 全 accept → patch 应用 + 新 plan 节点变化 =====


async def test_merge_all_accept_applies_patches(
    merger: ReviewMerger, mock_router: AsyncMock
) -> None:
    c1 = _comment_row()
    cid = c1.id
    mock_router.complete_structured.return_value = _mock_response(
        MergerResult(
            actions=[
                MergerAction(
                    comment_id=cid,
                    decision="accept",
                    reason="合理",
                    patch=CriticAction(
                        node_index=0,
                        action="replace",
                        reason="按评论修订",
                        replacement=ParagraphNode(text="revised paragraph"),
                    ),
                ),
            ],
            overall_comment="已修订",
        )
    )

    new_plan, result = await merger.merge(
        plan=_plan(), comments=[c1], adapter=_adapter(),
    )
    mock_router.complete_structured.assert_called_once()
    assert cast(ParagraphNode, new_plan.nodes[0]).text == "revised paragraph"
    assert result.actions[0].decision == "accept"


# ===== Case 3: 全 reject → 返回原 plan + actions 齐 =====


async def test_merge_all_reject_returns_original_plan(
    merger: ReviewMerger, mock_router: AsyncMock
) -> None:
    c1 = _comment_row()
    cid = c1.id
    plan = _plan()
    mock_router.complete_structured.return_value = _mock_response(
        MergerResult(
            actions=[
                MergerAction(
                    comment_id=cid,
                    decision="reject",
                    reason="评论不可执行",
                    patch=None,
                ),
            ],
            overall_comment="无修改",
        )
    )

    new_plan, result = await merger.merge(
        plan=plan, comments=[c1], adapter=_adapter(),
    )
    assert new_plan is plan
    assert len(result.actions) == 1
    assert result.actions[0].patch is None


# ===== Case 4: 非法 patch（node_index 越界）→ 跳过不崩 =====


async def test_merge_out_of_bounds_patch_skipped(
    merger: ReviewMerger, mock_router: AsyncMock
) -> None:
    c1 = _comment_row()
    cid = c1.id
    plan = _plan()
    mock_router.complete_structured.return_value = _mock_response(
        MergerResult(
            actions=[
                MergerAction(
                    comment_id=cid,
                    decision="accept",
                    reason="尝试越界",
                    patch=CriticAction(
                        node_index=99,
                        action="remove",
                        reason="越界删除",
                    ),
                ),
            ],
        )
    )

    new_plan, result = await merger.merge(
        plan=plan, comments=[c1], adapter=_adapter(),
    )
    # 越界 patch 被 _apply_critic log+skip，节点数不变
    assert len(new_plan.nodes) == 2
    assert result.actions[0].decision == "accept"


# ===== Case 5: 混合 accept/reject/partial → 只应用有 patch 的 =====


async def test_merge_mixed_decisions(
    merger: ReviewMerger, mock_router: AsyncMock
) -> None:
    c1, c2, c3 = _comment_row(), _comment_row(), _comment_row()
    mock_router.complete_structured.return_value = _mock_response(
        MergerResult(
            actions=[
                MergerAction(
                    comment_id=c1.id,
                    decision="accept",
                    reason="采纳",
                    patch=CriticAction(
                        node_index=0,
                        action="replace",
                        reason="r1",
                        replacement=ParagraphNode(text="accepted change"),
                    ),
                ),
                MergerAction(
                    comment_id=c2.id,
                    decision="reject",
                    reason="驳回",
                    patch=None,
                ),
                MergerAction(
                    comment_id=c3.id,
                    decision="partial",
                    reason="部分采纳",
                    patch=CriticAction(
                        node_index=1,
                        action="insert_after",
                        reason="r3",
                        replacement=ParagraphNode(text="extra note"),
                    ),
                ),
            ],
        )
    )

    new_plan, result = await merger.merge(
        plan=_plan(), comments=[c1, c2, c3], adapter=_adapter(),
    )
    assert len(result.actions) == 3
    assert cast(ParagraphNode, new_plan.nodes[0]).text == "accepted change"
    assert len(new_plan.nodes) == 3
    assert cast(ParagraphNode, new_plan.nodes[2]).text == "extra note"


# ===== 辅助：_comments_to_prompt_json 只保留约定字段 =====


def test_comments_to_prompt_json_strips_internal_fields() -> None:
    c = _comment_row()
    payload = _comments_to_prompt_json([c])
    assert str(c.id) in payload
    assert '"body": "please revise"' in payload
    assert "session_id" not in payload
    assert "resolved" not in payload
