"""Task 15 Tool ABC / 数据类 / 协议 / 异常层级 的单元测试。

所有用例不依赖 registry / 真实工具，FakeTool 只用于验证 ABC 契约。
"""

from collections.abc import Sequence
from dataclasses import FrozenInstanceError
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import BaseModel, ConfigDict

from app.tools.base import (
    ExecContext,
    ShellResult,
    StdoutEmitter,
    Tool,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolResult,
    ToolValidationError,
)


class FakeArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class FakeTool(Tool[FakeArgs]):
    name = "fake"
    description = "Fake tool for tests"
    args_schema = FakeArgs

    async def execute(self, args: FakeArgs, ctx: ExecContext) -> ToolResult:
        return ToolResult(ok=True, output=args.text)


class NonRerunnableTool(FakeTool):
    name = "fake_no_rerun"
    rerunnable = False


class IncompleteTool(Tool[FakeArgs]):
    """缺少 execute 实现的抽象子类，用于验证 ABC 实例化约束。"""

    name = "incomplete"
    description = "missing execute"
    args_schema = FakeArgs


class RecordingEmitter:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    async def emit(self, chunk: str) -> None:
        self.chunks.append(chunk)


class _FakeShell:
    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        return ShellResult(exit_code=0, stdout="", stderr="")


def _ctx(emitter: StdoutEmitter | None = None) -> ExecContext:
    return ExecContext(
        session_id=uuid4(),
        plan_version=1,
        step_id="step_001",
        workdir=Path("/sandbox/work"),
        run_shell=_FakeShell(),
        emit_stdout=emitter,
    )


# ===== T1-T3: Tool ABC =====


def test_minimal_tool_subclass_exposes_metadata() -> None:
    tool = FakeTool()
    assert tool.name == "fake"
    assert tool.description == "Fake tool for tests"
    assert tool.args_schema is FakeArgs
    assert tool.rerunnable is True


def test_json_schema_matches_args_schema() -> None:
    tool = FakeTool()
    assert tool.json_schema == FakeArgs.model_json_schema()
    assert tool.json_schema["type"] == "object"


def test_rerunnable_false_preserved() -> None:
    tool = NonRerunnableTool()
    assert tool.rerunnable is False


# ===== T4-T5: ToolResult =====


def test_tool_result_defaults() -> None:
    r = ToolResult(ok=False, output="nope")
    assert r.ok is False
    assert r.output == "nope"
    assert r.artifacts == []
    assert r.git_commit is None


def test_tool_result_artifacts_relative_paths() -> None:
    r = ToolResult(ok=True, output="done", artifacts=[Path("src/main.py")])
    assert r.artifacts == [Path("src/main.py")]
    # 序列化用 str(Path)（Windows 上是反斜杠），断言平台无关
    assert r.model_dump(mode="json")["artifacts"] == [str(Path("src/main.py"))]


# ===== T6: ExecContext =====


def test_exec_context_frozen_with_defaults() -> None:
    ctx = _ctx()
    assert ctx.emit_stdout is None
    assert ctx.workdir == Path("/sandbox/work")
    with pytest.raises(FrozenInstanceError):
        ctx.workdir = Path("/elsewhere")  # type: ignore[misc]


# ===== T7-T8: _emit =====


async def test_emit_forwards_chunk_when_assembled() -> None:
    emitter = RecordingEmitter()
    tool = FakeTool()
    await tool._emit(_ctx(emitter), "hello\n")
    assert emitter.chunks == ["hello\n"]


async def test_emit_noop_when_not_assembled() -> None:
    tool = FakeTool()
    await tool._emit(_ctx(None), "ignored")


# ===== T9: 异常层级 =====


def test_exception_hierarchy() -> None:
    e = ToolNotFoundError("nope")
    assert isinstance(e, ToolError)
    assert isinstance(e, KeyError)
    assert isinstance(ToolValidationError("bad"), ToolError)
    assert isinstance(ToolExecutionError("boom"), ToolError)


# ===== T10: 抽象约束 =====


def test_abstract_tool_cannot_instantiate() -> None:
    with pytest.raises(TypeError):
        IncompleteTool()  # type: ignore[abstract]
