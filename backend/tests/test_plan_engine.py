"""Plan 引擎测试（全部 mock LLM，不真调 API）。"""

from typing import cast
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.plan_engine import PlanEngine, _apply_critic, _assign_ids
from app.core.plan_schemas import (
    CriticAction,
    CriticResult,
    DecisionNode,
    GlossaryNode,
    HeadingNode,
    ParagraphNode,
    PlanDocument,
    StepNode,
)
from app.llm.prompts.loader import load_prompt as _load_prompt
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


def _mock_response(parsed: PlanDocument | CriticResult) -> MagicMock:
    """构造一个 mock 的 StructuredResponse，只暴露 .parsed 属性。"""
    mock = MagicMock()
    mock.parsed = parsed
    return mock


# ===== Fixtures =====


@pytest.fixture
def mock_router() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def plan_engine(mock_router: AsyncMock) -> PlanEngine:
    return PlanEngine(mock_router)


# ===== T1: assign_ids 顺序编号 =====


def test_assign_ids_numbers_sequentially() -> None:
    plan = PlanDocument(
        title="Test",
        summary="Summary",
        nodes=[
            StepNode(title="s1", description="d1", tool="shell"),
            StepNode(title="s2", description="d2", tool="shell"),
            StepNode(title="s3", description="d3", tool="shell"),
        ],
    )
    result = _assign_ids(plan)
    assert cast(StepNode, result.nodes[0]).id == "step_001"
    assert cast(StepNode, result.nodes[1]).id == "step_002"
    assert cast(StepNode, result.nodes[2]).id == "step_003"


# ===== T2: assign_ids 跳过 paragraph 和 heading =====


def test_assign_ids_skips_paragraph_and_heading() -> None:
    plan = PlanDocument(
        title="Test",
        summary="Summary",
        nodes=[
            HeadingNode(level=1, text="H1"),
            ParagraphNode(text="p1"),
            StepNode(title="s1", description="d1", tool="shell"),
        ],
    )
    result = _assign_ids(plan)
    assert getattr(result.nodes[0], "id", "") == ""
    assert getattr(result.nodes[1], "id", "") == ""
    assert cast(StepNode, result.nodes[2]).id == "step_001"


# ===== T3: assign_ids glossary =====


def test_assign_ids_glossary() -> None:
    plan = PlanDocument(
        title="Test",
        summary="Summary",
        nodes=[
            GlossaryNode(term="t1", definition="d1"),
            GlossaryNode(term="t2", definition="d2"),
        ],
    )
    result = _assign_ids(plan)
    assert cast(GlossaryNode, result.nodes[0]).id == "gls_001"
    assert cast(GlossaryNode, result.nodes[1]).id == "gls_002"


# ===== T4: apply_critic remove =====


def test_apply_critic_remove() -> None:
    plan = PlanDocument(
        title="Test",
        summary="Summary",
        nodes=[
            StepNode(title="s1", description="d1", tool="shell"),
            StepNode(title="s2", description="d2", tool="shell"),
        ],
    )
    plan = _assign_ids(plan)
    critic = CriticResult(
        actions=[CriticAction(node_index=0, action="remove", reason="remove first")]
    )
    result = _apply_critic(plan, critic)
    assert len(result.nodes) == 1
    assert cast(StepNode, result.nodes[0]).title == "s2"


# ===== T5: apply_critic replace =====


def test_apply_critic_replace() -> None:
    plan = PlanDocument(
        title="Test",
        summary="Summary",
        nodes=[StepNode(title="s1", description="d1", tool="shell")],
    )
    plan = _assign_ids(plan)
    critic = CriticResult(
        actions=[
            CriticAction(
                node_index=0,
                action="replace",
                reason="replace",
                replacement=StepNode(
                    title="s1-new", description="d1-new", tool="fs.read"
                ),
            )
        ]
    )
    result = _apply_critic(plan, critic)
    assert cast(StepNode, result.nodes[0]).title == "s1-new"


# ===== T6: apply_critic insert_after =====


def test_apply_critic_insert_after() -> None:
    plan = PlanDocument(
        title="Test",
        summary="Summary",
        nodes=[StepNode(title="s1", description="d1", tool="shell")],
    )
    plan = _assign_ids(plan)
    critic = CriticResult(
        actions=[
            CriticAction(
                node_index=0,
                action="insert_after",
                reason="insert",
                replacement=StepNode(title="s2", description="d2", tool="shell"),
            )
        ]
    )
    result = _apply_critic(plan, critic)
    assert len(result.nodes) == 2
    assert cast(StepNode, result.nodes[0]).title == "s1"
    assert cast(StepNode, result.nodes[1]).title == "s2"


# ===== T7: apply_critic 越界跳过 =====


def test_apply_critic_out_of_bounds_skipped() -> None:
    plan = PlanDocument(
        title="Test",
        summary="Summary",
        nodes=[StepNode(title="s1", description="d1", tool="shell")],
    )
    plan = _assign_ids(plan)
    critic = CriticResult(
        actions=[CriticAction(node_index=99, action="remove", reason="oob")]
    )
    result = _apply_critic(plan, critic)
    assert len(result.nodes) == 1


# ===== T8: apply_critic 重新分配 id =====


def test_apply_critic_reassigns_ids() -> None:
    plan = PlanDocument(
        title="Test",
        summary="Summary",
        nodes=[
            StepNode(title="s1", description="d1", tool="shell"),
            StepNode(title="s2", description="d2", tool="shell"),
        ],
    )
    plan = _assign_ids(plan)
    assert cast(StepNode, plan.nodes[0]).id == "step_001"
    assert cast(StepNode, plan.nodes[1]).id == "step_002"
    critic = CriticResult(
        actions=[CriticAction(node_index=0, action="remove", reason="remove first")]
    )
    result = _apply_critic(plan, critic)
    assert len(result.nodes) == 1
    assert cast(StepNode, result.nodes[0]).id == "step_001"


# ===== T9: generate 调用 planner 然后 critic =====


async def test_generate_calls_planner_then_critic(
    plan_engine: PlanEngine, mock_router: AsyncMock
) -> None:
    planner_output = PlanDocument(
        title="Plan",
        summary="Summary",
        nodes=[StepNode(title="s1", description="d1", tool="shell")],
    )
    critic_output = CriticResult(actions=[])

    mock_router.complete_structured.side_effect = [
        _mock_response(planner_output),
        _mock_response(critic_output),
    ]

    result = await plan_engine.generate(
        init_request="build a web app",
        adapter=_adapter(),
    )

    assert mock_router.complete_structured.call_count == 2
    assert result.title == "Plan"
    assert cast(StepNode, result.nodes[0]).id == "step_001"


# ===== T10: generate 传递 ltm_recall 到 prompt =====


async def test_generate_passes_ltm_recall_to_prompt(
    plan_engine: PlanEngine, mock_router: AsyncMock
) -> None:
    planner_output = PlanDocument(
        title="Plan",
        summary="Summary",
        nodes=[StepNode(title="s1", description="d1", tool="shell")],
    )
    critic_output = CriticResult(actions=[])

    mock_router.complete_structured.side_effect = [
        _mock_response(planner_output),
        _mock_response(critic_output),
    ]

    await plan_engine.generate(
        init_request="build a web app",
        adapter=_adapter(),
        ltm_recall=["memory1", "memory2"],
    )

    planner_call = mock_router.complete_structured.call_args_list[0]
    assert "memory1" in planner_call.kwargs["user"]
    assert "memory2" in planner_call.kwargs["user"]


# ===== T11: generate 默认工具 =====


async def test_generate_default_tools(
    plan_engine: PlanEngine, mock_router: AsyncMock
) -> None:
    planner_output = PlanDocument(
        title="Plan",
        summary="Summary",
        nodes=[StepNode(title="s1", description="d1", tool="shell")],
    )
    critic_output = CriticResult(actions=[])

    mock_router.complete_structured.side_effect = [
        _mock_response(planner_output),
        _mock_response(critic_output),
    ]

    await plan_engine.generate(
        init_request="build a web app",
        adapter=_adapter(),
    )

    planner_call = mock_router.complete_structured.call_args_list[0]
    assert "shell" in planner_call.kwargs["user"]


# ===== T12: PlanDocument schema 有效 =====


def test_plan_document_schema_valid() -> None:
    schema = PlanDocument.model_json_schema()
    assert "$defs" in schema
    assert "properties" in schema


# ===== T13: decision blocking 始终为 True =====


def test_decision_blocking_always_true() -> None:
    d = DecisionNode(question="q?", options=["a", "b"])
    assert d.blocking is True


# ===== T14: _load_prompt 正确读取文件 =====


def test_load_prompt_reads_file() -> None:
    content = _load_prompt("planner.md")
    assert "项目规划师" in content


def test_load_prompt_rejects_path_traversal() -> None:
    with pytest.raises(ValueError, match="Invalid prompt filename"):
        _load_prompt("../../../etc/passwd")


# ===== T15: apply_critic 未知 action 跳过 =====


def test_apply_critic_unknown_action_skipped() -> None:
    plan = PlanDocument(
        title="Test",
        summary="Summary",
        nodes=[StepNode(title="s1", description="d1", tool="shell")],
    )
    plan = _assign_ids(plan)
    critic = CriticResult(
        actions=[CriticAction(node_index=0, action="remvoe", reason="typo")]
    )
    result = _apply_critic(plan, critic)
    assert len(result.nodes) == 1
    assert cast(StepNode, result.nodes[0]).title == "s1"
