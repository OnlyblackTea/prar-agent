"""M4-23 long_term 模块单测：内容模板纯函数 + record_episodic（100% mock）。"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.embedding import EmbeddingTransportError
from app.core.plan_schemas import DecisionNode, PlanDocument, StepNode
from app.db.models import Memory
from app.memory.long_term import (
    LongTermMemory,
    RunSummary,
    StepOutcome,
    build_episodic_content,
)
from app.services.memory_service import MemoryService


def _full_plan() -> PlanDocument:
    return PlanDocument(
        title="部署 Kafka",
        summary="安装并配置 Kafka 集群",
        nodes=[
            DecisionNode(
                question="部署方式",
                kind="single_choice",
                options=["k8s", "裸机"],
                answer="k8s",
            ),
            DecisionNode(
                question="副本数",
                kind="single_choice",
                options=["1", "3"],
            ),
            StepNode(title="安装 helm", description="d", tool="shell"),
            StepNode(title="部署 chart", description="d", tool="shell"),
        ],
    )


# ===== L1: builder 模板 =====


def test_builder_only_answered_decisions_and_no_run_section() -> None:
    content = build_episodic_content(
        init_request="搭一个 Kafka",
        plan_version=2,
        plan=_full_plan(),
        run=None,
    )
    lines = content.splitlines()
    assert lines[0] == "需求：搭一个 Kafka"
    assert lines[1] == "计划 v2：《部署 Kafka》— 安装并配置 Kafka 集群"
    assert "决策：" in lines
    assert "- 部署方式 = k8s" in lines
    assert "副本数" not in content
    assert "1. 安装 helm（工具 shell）" in lines
    assert "2. 部署 chart（工具 shell）" in lines
    assert "执行结果" not in content


def test_builder_minimal_plan_no_empty_sections() -> None:
    content = build_episodic_content(
        init_request="r",
        plan_version=1,
        plan=PlanDocument(title="t", summary="", nodes=[]),
        run=None,
    )
    assert content == "需求：r\n计划 v1：《t》"


# ===== L2: 执行结果段 =====


def test_builder_run_results_ok_fail() -> None:
    run = RunSummary(
        plan_version=2,
        all_ok=False,
        steps=[
            StepOutcome(step_id="step_000", ok=True, git_commit="cafebabe"),
            StepOutcome(step_id="step_001", ok=False),
            StepOutcome(step_id="step_002", ok=True),
        ],
    )
    content = build_episodic_content(
        init_request="r",
        plan_version=2,
        plan=_full_plan(),
        run=run,
    )
    assert "执行结果：" in content
    assert "1. step_000：成功（commit cafebabe）" in content
    assert "2. step_001：失败" in content
    assert "3. step_002：成功" in content
    assert "（commit None）" not in content


# ===== L3: record_episodic 转发 =====


async def test_record_episodic_forwards_to_store() -> None:
    sid = uuid4()
    plan = _full_plan()
    run = RunSummary(
        plan_version=2,
        all_ok=True,
        steps=[StepOutcome(step_id="step_000", ok=True, git_commit="abc")],
    )
    row = MagicMock(spec=Memory)
    store = MagicMock(spec=MemoryService)
    store.store = AsyncMock(return_value=row)

    result = await LongTermMemory(store).record_episodic(
        session_id=sid,
        init_request="搭一个 Kafka",
        plan_version=2,
        plan=plan,
        run=run,
    )

    assert result is row
    store.store.assert_awaited_once()
    kwargs = store.store.await_args.kwargs
    assert kwargs["kind"] == "episodic"
    assert kwargs["content"] == build_episodic_content(
        init_request="搭一个 Kafka", plan_version=2, plan=plan, run=run,
    )
    assert kwargs["source_session"] == sid
    assert kwargs["importance"] == 0.5
    assert kwargs.get("user_id") is None


# ===== L4: embedding 错误上抛 =====


async def test_record_episodic_passes_through_embedding_error() -> None:
    store = MagicMock(spec=MemoryService)
    store.store = AsyncMock(
        side_effect=EmbeddingTransportError("boom", model_id="m"),
    )
    with pytest.raises(EmbeddingTransportError):
        await LongTermMemory(store).record_episodic(
            session_id=uuid4(),
            init_request="r",
            plan_version=1,
            plan=_full_plan(),
            run=None,
        )
