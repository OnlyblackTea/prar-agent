"""Action dispatcher 测试（FakeActor 注入，零真调 LLM；真实 Sandbox + 真实内置工具）。"""

import sys
from pathlib import Path
from typing import Any, ClassVar
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from app.core.action_dispatcher import (
    ActionDispatcher,
    ActorAction,
    LLMActor,
    StepExecution,
    create_default_dispatcher,
)
from app.core.plan_schemas import PlanDocument, StepNode
from app.llm.types import ResolvedAdapter
from app.tools.base import ExecContext, Tool, ToolExecutionError, ToolResult
from app.tools.builtin import builtin_tools
from app.tools.registry import ToolRegistry

IS_WIN = sys.platform == "win32"
_FAIL_CMD = "exit /b 3" if IS_WIN else "exit 3"
_ECHO_OK = "echo hello17"


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


def _mock_response(parsed: ActorAction) -> MagicMock:
    mock = MagicMock()
    mock.parsed = parsed
    return mock


def make_step(
    tool: str,
    tool_args: dict[str, Any],
    *,
    step_id: str = "step_001",
    rerunnable: bool = True,
) -> StepNode:
    return StepNode(
        id=step_id,
        title=f"step {step_id}",
        description="test step",
        tool=tool,
        tool_args=tool_args,
        rerunnable=rerunnable,
    )


def make_plan(*steps: StepNode) -> PlanDocument:
    return PlanDocument(title="test plan", summary="", nodes=list(steps))


class FakeActor:
    """scripted 决策序列：每轮 decide 弹出预设 ActorAction 并记录调用。"""

    def __init__(self, actions: list[ActorAction]) -> None:
        self._actions = list(actions)
        self.calls: list[tuple[str, list[str]]] = []

    async def decide(self, *, step: StepNode, observations: list[str]) -> ActorAction:
        self.calls.append((step.id, list(observations)))
        if not self._actions:
            raise AssertionError("FakeActor out of scripted actions")
        return self._actions.pop(0)


class _BoomArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")


class _BoomTool(Tool[_BoomArgs]):
    """测试工具：execute 恒抛环境故障。"""

    name: ClassVar[str] = "boom"
    description: ClassVar[str] = "raises ToolExecutionError"
    args_schema: ClassVar[type[BaseModel]] = _BoomArgs

    async def execute(self, args: _BoomArgs, ctx: ExecContext) -> ToolResult:
        raise ToolExecutionError("boom")


@pytest.fixture
def dispatcher(tmp_path: Path) -> tuple[ActionDispatcher, FakeActor, Path]:
    """真实 registry（内置工具）+ FakeActor + 临时沙箱基目录。"""
    registry = ToolRegistry()
    for t in builtin_tools():
        registry.register(t)
    actor = FakeActor([])
    base = tmp_path / "runs"
    return ActionDispatcher(registry, actor, sandbox_base=base), actor, base


def _act(**kwargs: Any) -> ActorAction:
    kwargs.setdefault("done", False)
    return ActorAction(**kwargs)


# ===== T1: 首轮成功 =====


async def test_write_step_succeeds_first_try(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    plan = make_plan(
        make_step("fs.write", {"path": "out.txt", "content": "hello"}),
    )
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, StepExecution)
    assert rec.ok is True
    assert rec.attempts == 1
    assert rec.artifacts == [Path("out.txt")]
    assert rec.thoughts == []
    assert rec.failure_reason is None
    assert "wrote" in rec.output
    assert actor.calls == []


# ===== T2: 失败 → LLM 修正成功 =====


async def test_shell_failure_then_corrected(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    actor._actions = [
        _act(thought="换一个能成功的命令", tool="shell", tool_args={"command": _ECHO_OK}),
    ]
    plan = make_plan(make_step("shell", {"command": _FAIL_CMD}))
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    rec = records[0]
    assert rec.ok is True
    assert rec.attempts == 2
    assert rec.thoughts == ["换一个能成功的命令"]
    assert rec.failure_reason is None
    assert "hello17" in rec.output
    assert len(actor.calls) == 1
    # 观察传递：LLM 看到了首轮失败输出
    assert "exit_code=3" in actor.calls[0][1][0]


# ===== T3: 修正轮耗尽 =====


async def test_correction_rounds_exhausted(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    # 与首轮参数不同（同参会被 rerunnable=False 拒绝拦截，见 T5）
    actor._actions = [
        _act(thought="重试 1", tool="shell", tool_args={"command": _FAIL_CMD + " "}),
        _act(thought="重试 2", tool="shell", tool_args={"command": _FAIL_CMD + "  "}),
    ]
    plan = make_plan(make_step("shell", {"command": _FAIL_CMD}))
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    rec = records[0]
    assert rec.ok is False
    assert rec.attempts == 3
    assert len(rec.thoughts) == 2
    assert rec.failure_reason is not None
    assert len(actor.calls) == 2


# ===== T4: actor 主动放弃（done=True） =====


async def test_actor_gives_up_done_true(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    actor._actions = [_act(thought="无法自动修正", done=True)]
    plan = make_plan(make_step("shell", {"command": _FAIL_CMD}))
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    rec = records[0]
    assert rec.ok is False
    assert rec.attempts == 1
    assert rec.thoughts == ["无法自动修正"]
    assert rec.failure_reason is not None
    assert "exit_code=3" in rec.output


# ===== T5: rerunnable 同参拒绝 =====


async def test_rerunnable_same_args_rejected(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    # shell 的 rerunnable=False；第一个 action 与首轮参数完全相同 → 拒绝
    actor._actions = [
        _act(thought="原样重试", tool="shell", tool_args={"command": _FAIL_CMD}),
        _act(thought="换参数", tool="shell", tool_args={"command": _ECHO_OK}),
    ]
    plan = make_plan(make_step("shell", {"command": _FAIL_CMD}, rerunnable=False))
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    rec = records[0]
    assert rec.ok is True
    assert rec.attempts == 2  # 拒绝不计 attempt
    assert len(actor.calls) == 2
    # 拒绝原因作为观察传回 LLM（第二轮能看到）
    assert any("[rejected]" in obs for obs in actor.calls[1][1])


# ===== T6: plan 提供未注册工具 → step failed =====


async def test_plan_unknown_tool_fails_step(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    plan = make_plan(make_step("ghost", {}))
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    rec = records[0]
    assert rec.ok is False
    assert rec.attempts == 0
    assert rec.failure_reason is not None
    assert "ghost" in rec.failure_reason
    assert actor.calls == []  # plan 契约错误不浪费 LLM 轮


# ===== T7: LLM 提出未注册工具 → attempt 计数（下轮仍可成功） =====


async def test_actor_unknown_tool_counts_attempt(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    actor._actions = [
        _act(thought="用不存在的工具", tool="ghost", tool_args={}),
        _act(thought="换回 shell", tool="shell", tool_args={"command": _ECHO_OK}),
    ]
    plan = make_plan(make_step("shell", {"command": _FAIL_CMD}))
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    rec = records[0]
    assert rec.ok is True
    assert rec.attempts == 2  # ghost 拒绝不计 attempt
    assert len(actor.calls) == 2
    assert any("[rejected]" in obs for obs in actor.calls[1][1])


# ===== T8: LLM tool_args 不符合 schema → attempt 计数 =====


async def test_actor_invalid_args_counts_attempt(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    actor._actions = [
        _act(thought="缺参数", tool="fs.write", tool_args={"content": "x"}),
        _act(thought="补参数", tool="fs.write", tool_args={"path": "ok.txt", "content": "ok"}),
    ]
    plan = make_plan(make_step("shell", {"command": _FAIL_CMD}))
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    rec = records[0]
    assert rec.ok is True
    assert rec.attempts == 2
    assert any("[rejected]" in obs for obs in actor.calls[1][1])


# ===== T9: plan tool_args 不符合 schema → step failed（不经 LLM） =====


async def test_plan_invalid_args_fails_step(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    plan = make_plan(make_step("fs.read", {"path": 123}))  # int 非 str
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    rec = records[0]
    assert rec.ok is False
    assert rec.attempts == 0
    assert rec.failure_reason is not None
    assert actor.calls == []


# ===== T10: fail-fast =====


async def test_fail_fast_stops_remaining_steps(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    actor._actions = [
        _act(thought="r1", tool="shell", tool_args={"command": _FAIL_CMD}),
        _act(thought="r2", tool="shell", tool_args={"command": _FAIL_CMD}),
    ]
    plan = make_plan(
        make_step("shell", {"command": _FAIL_CMD}, step_id="step_001"),
        make_step("fs.write", {"path": "never.txt", "content": "x"}, step_id="step_002"),
    )
    records = await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)
    assert len(records) == 1  # step_002 未执行
    assert records[0].ok is False
    assert len(actor.calls) == 2  # 只有 step_001 的两轮


# ===== T11: workdir 布局 + step 间文件可见 =====


async def test_workdir_layout_and_cross_step_visibility(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, _, base = dispatcher
    session_id = uuid4()
    # Windows cmd 中 / 开头的段会被当选项开关，路径必须全反斜杠
    cat_prev = "type ..\\step_001\\a.txt" if IS_WIN else "cat ../step_001/a.txt"
    plan = make_plan(
        make_step("fs.write", {"path": "a.txt", "content": "first"}, step_id="step_001"),
        make_step("shell", {"command": cat_prev}, step_id="step_002"),
    )
    records = await disp.execute_plan(plan, session_id=session_id, plan_version=1)
    assert len(records) == 2
    assert all(r.ok for r in records)
    root = base / str(session_id)
    assert (root / "steps" / "step_001" / "a.txt").read_text(encoding="utf-8") == "first"
    assert (root / "steps" / "step_002").is_dir()
    assert "first" in records[1].output


# ===== T12: 空 plan =====


async def test_empty_plan_returns_empty(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, actor, _ = dispatcher
    records = await disp.execute_plan(make_plan(), session_id=uuid4(), plan_version=1)
    assert records == []
    assert actor.calls == []


# ===== T13: 沙箱根布局 =====


async def test_sandbox_root_under_base(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, _, base = dispatcher
    session_id = uuid4()
    plan = make_plan(make_step("fs.write", {"path": "x.txt", "content": "x"}))
    await disp.execute_plan(plan, session_id=session_id, plan_version=1)
    assert (base / str(session_id)).is_dir()
    assert (base / str(session_id) / "steps" / "step_001").is_dir()


# ===== T14: step 无 id → 序号 workdir =====


async def test_step_without_id_gets_index_workdir(
    dispatcher: tuple[ActionDispatcher, FakeActor, Path],
) -> None:
    disp, _, base = dispatcher
    session_id = uuid4()
    plan = make_plan(
        make_step("fs.write", {"path": "a.txt", "content": "a"}, step_id=""),
        make_step("fs.write", {"path": "b.txt", "content": "b"}, step_id=""),
    )
    records = await disp.execute_plan(plan, session_id=session_id, plan_version=1)
    assert len(records) == 2
    root = base / str(session_id)
    assert (root / "steps" / "step_000" / "a.txt").exists()
    assert (root / "steps" / "step_001" / "b.txt").exists()


# ===== T15: 环境故障向上抛 =====


async def test_tool_execution_error_propagates(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(_BoomTool())
    disp = ActionDispatcher(registry, FakeActor([]), sandbox_base=tmp_path / "runs")
    plan = make_plan(make_step("boom", {}))
    with pytest.raises(ToolExecutionError, match="boom"):
        await disp.execute_plan(plan, session_id=uuid4(), plan_version=1)


# ===== T16: LLMActor 用 complete_structured =====


async def test_llm_actor_calls_complete_structured(tmp_path: Path) -> None:
    router = AsyncMock()
    adapter = _adapter()
    actor = LLMActor(router, adapter)
    router.complete_structured.return_value = _mock_response(
        _act(thought="ok", done=True),
    )
    action = await actor.decide(
        step=make_step("shell", {"command": _FAIL_CMD}),
        observations=["exit_code=3"],
    )
    assert action.done is True
    assert action.thought == "ok"
    kwargs = router.complete_structured.call_args.kwargs
    assert kwargs["adapter"] is adapter
    assert kwargs["schema"] is ActorAction
    assert "exit_code=3" in kwargs["user"]


# ===== T17: 默认工厂注册内置工具 =====


def test_create_default_dispatcher_registers_builtin(tmp_path: Path) -> None:
    router = AsyncMock()
    disp = create_default_dispatcher(router, _adapter())
    assert disp.registry.list_names() == ["shell", "fs.read", "fs.write"]
