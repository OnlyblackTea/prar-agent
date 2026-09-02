"""Task 17 内置工具（shell / fs.read / fs.write）的单元测试。

T4-T18 全部基于真实 Sandbox（tmp_path 沙箱根）+ 真实 ExecContext 实跑；
命令/助手按平台分支（Windows cmd vs POSIX sh），双平台均真实执行不 mock。
T19-T23（Task 19 流式管道）：FakeRunner / 发射器替身 + 真实沙箱端到端。
"""

import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.tools.base import ExecContext, ShellResult, ShellRunner
from app.tools.builtin import builtin_tools
from app.tools.builtin.fs import FsReadArgs, FsReadTool, FsWriteArgs, FsWriteTool
from app.tools.builtin.shell import ShellArgs, ShellTool
from app.tools.registry import ToolRegistry
from app.tools.sandbox import Sandbox, SandboxLimits

IS_WIN = sys.platform == "win32"

_ECHO_OK = "echo hello17"
_FAIL_CMD = "exit /b 3" if IS_WIN else "exit 3"
_SLEEP_CMD = "ping -n 11 127.0.0.1 >nul" if IS_WIN else "sleep 10"
_PWD_CMD = "cd" if IS_WIN else "pwd"
_ABS_PATH = "C:/Windows/system32" if IS_WIN else "/etc/passwd"


def _cat_cmd(rel: str) -> str:
    return f"type {rel}" if IS_WIN else f"cat {rel}"


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    # max_processes=0（不限制）：RLIMIT_NPROC 按用户全量进程数计算，
    # 宿主若已有多进程则外部命令 fork 会失败；NPROC 限制由 test_sandbox.py 专测
    sb = Sandbox(
        tmp_path / "sb",
        limits=SandboxLimits(max_memory_mb=256, max_cpu_seconds=30, max_processes=0),
    )
    sb.ensure_root()
    return sb


def _ctx(runner: ShellRunner, workdir: str = ".", **kw: Any) -> ExecContext:
    return ExecContext(
        session_id=uuid4(),
        plan_version=1,
        step_id="step_001",
        workdir=Path(workdir),
        run_shell=runner,
        **kw,
    )


# ===== T1-T3: 元数据 / schema / 工厂 =====


def test_tool_metadata() -> None:
    tools = builtin_tools()
    assert [t.name for t in tools] == ["shell", "fs.read", "fs.write"]
    assert all(t.description for t in tools)
    # rerunnable：shell 副作用不可知保守 False；fs 类幂等 True
    assert tools[0].rerunnable is False
    assert tools[1].rerunnable is True
    assert tools[2].rerunnable is True


def test_json_schema_no_additional_properties() -> None:
    # extra="forbid" 生效：additionalProperties 永不为 True
    # （pydantic 2.13 输出 False 键；旧版不输出 → get 默认 False）
    for t in builtin_tools():
        assert t.json_schema.get("additionalProperties", False) is not True


def test_registry_roundtrip() -> None:
    reg = ToolRegistry()
    for t in builtin_tools():
        reg.register(t)
    assert reg.list_names() == ["shell", "fs.read", "fs.write"]
    specs = reg.to_specs()
    assert [s.name for s in specs] == ["shell", "fs.read", "fs.write"]
    assert specs[0].parameters["properties"]["command"]["type"] == "string"


# ===== T4-T7: shell =====


async def test_shell_success(sandbox: Sandbox) -> None:
    r = await ShellTool().execute(ShellArgs(command=_ECHO_OK), _ctx(sandbox))
    assert r.ok is True
    assert r.output.startswith("exit_code=0\n")
    assert "hello17" in r.output


async def test_shell_failure(sandbox: Sandbox) -> None:
    r = await ShellTool().execute(ShellArgs(command=_FAIL_CMD), _ctx(sandbox))
    assert r.ok is False
    assert "exit_code=3" in r.output


async def test_shell_timeout(sandbox: Sandbox) -> None:
    r = await ShellTool().execute(ShellArgs(command=_SLEEP_CMD, timeout=1), _ctx(sandbox))
    assert r.ok is False
    assert "exit_code=124" in r.output
    assert "timeout" in r.output


async def test_shell_cwd(sandbox: Sandbox) -> None:
    (sandbox.root / "sub").mkdir()
    r = await ShellTool().execute(ShellArgs(command=_PWD_CMD), _ctx(sandbox, workdir="sub"))
    assert r.ok is True
    assert str(sandbox.root / "sub") in r.output


# ===== T8-T13: fs.read =====


async def test_fs_read_text(sandbox: Sandbox) -> None:
    (sandbox.root / "a.txt").write_text("plain", encoding="utf-8")
    r = await FsReadTool().execute(FsReadArgs(path="a.txt"), _ctx(sandbox))
    assert r.ok is True
    assert r.output == "plain"


async def test_fs_read_missing(sandbox: Sandbox) -> None:
    r = await FsReadTool().execute(FsReadArgs(path="nope.txt"), _ctx(sandbox))
    assert r.ok is False
    assert "file not found" in r.output


async def test_fs_read_directory(sandbox: Sandbox) -> None:
    (sandbox.root / "d").mkdir()
    r = await FsReadTool().execute(FsReadArgs(path="d"), _ctx(sandbox))
    assert r.ok is False
    assert "is a directory" in r.output


async def test_fs_read_binary(sandbox: Sandbox) -> None:
    (sandbox.root / "bin.dat").write_bytes(b"\xff\xfe\x00\x01")
    r = await FsReadTool().execute(FsReadArgs(path="bin.dat"), _ctx(sandbox))
    assert r.ok is False
    assert "binary" in r.output
    assert "4 bytes" in r.output


async def test_fs_read_truncated(sandbox: Sandbox) -> None:
    (sandbox.root / "big.txt").write_text("x" * 2000, encoding="utf-8")
    r = await FsReadTool().execute(FsReadArgs(path="big.txt", max_bytes=1024), _ctx(sandbox))
    assert r.ok is True
    assert r.output.endswith("[truncated: read 1024 of 2000 bytes]")
    assert len(r.output) < 2000


@pytest.mark.parametrize("bad_path", ["../escape.txt", "..", _ABS_PATH])
async def test_fs_read_escape(bad_path: str, sandbox: Sandbox) -> None:
    r = await FsReadTool().execute(FsReadArgs(path=bad_path), _ctx(sandbox))
    assert r.ok is False


# ===== T14-T17: fs.write =====


async def test_fs_write_new_file(sandbox: Sandbox) -> None:
    r = await FsWriteTool().execute(FsWriteArgs(path="a.txt", content="你好"), _ctx(sandbox))
    assert r.ok is True
    assert "wrote 6 bytes" in r.output
    assert r.artifacts == [Path("a.txt")]
    assert (sandbox.root / "a.txt").read_text(encoding="utf-8") == "你好"


async def test_fs_write_overwrite(sandbox: Sandbox) -> None:
    (sandbox.root / "a.txt").write_text("old", encoding="utf-8")
    r = await FsWriteTool().execute(FsWriteArgs(path="a.txt", content="new"), _ctx(sandbox))
    assert r.ok is True
    assert (sandbox.root / "a.txt").read_text(encoding="utf-8") == "new"


async def test_fs_write_nested_dirs(sandbox: Sandbox) -> None:
    r = await FsWriteTool().execute(
        FsWriteArgs(path="a/b/c.txt", content="deep"), _ctx(sandbox)
    )
    assert r.ok is True
    assert (sandbox.root / "a" / "b" / "c.txt").read_text(encoding="utf-8") == "deep"


@pytest.mark.parametrize("bad_path", ["../escape.txt", "..", _ABS_PATH])
async def test_fs_write_escape(bad_path: str, sandbox: Sandbox) -> None:
    r = await FsWriteTool().execute(FsWriteArgs(path=bad_path, content="x"), _ctx(sandbox))
    assert r.ok is False
    # 逃逸内容绝不能落盘在沙箱外
    assert not (sandbox.root.parent / "escape.txt").exists()


# ===== T18: 工具间协作闭环 =====


async def test_roundtrip_write_then_shell_cat(sandbox: Sandbox) -> None:
    w = await FsWriteTool().execute(
        FsWriteArgs(path="hello.txt", content="闭环协作"), _ctx(sandbox)
    )
    assert w.ok is True
    r = await ShellTool().execute(ShellArgs(command=_cat_cmd("hello.txt")), _ctx(sandbox))
    assert r.ok is True
    assert "闭环协作" in r.output


# ===== T19-T23: shell 流式装配（Task 19 真流式管道） =====


class _StdoutEmitter:
    def __init__(self) -> None:
        self.chunks: list[str] = []

    async def emit(self, chunk: str) -> None:
        self.chunks.append(chunk)


class _EventEmitter:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def emit(self, event: dict[str, Any]) -> None:
        self.events.append(event)


class _FakeRunner:
    """ShellRunner 替身：按脚本逐行回调 on_stdout 并记录转发。"""

    def __init__(self, exit_code: int = 0) -> None:
        self._exit_code = exit_code
        self.on_stdout_calls: list[str] = []

    async def run(
        self,
        argv: Sequence[str],
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: Path | None = None,
        on_stdout: Callable[[str], Awaitable[None]] | None = None,
    ) -> ShellResult:
        if on_stdout is not None:
            for line in ("alpha\n", "beta\n"):
                await on_stdout(line)
                self.on_stdout_calls.append(line)
        return ShellResult(exit_code=self._exit_code, stdout="alpha\nbeta\n", stderr="")


async def test_shell_forwards_stdout_to_emitter() -> None:
    """on_stdout 装配：FakeRunner 逐行回调 → ShellTool 转发 ctx.emit_stdout。"""
    runner = _FakeRunner()
    emitter = _StdoutEmitter()
    ctx = _ctx(runner, emit_stdout=emitter)
    r = await ShellTool().execute(ShellArgs(command="echo x"), ctx)
    assert r.ok is True
    assert runner.on_stdout_calls == ["alpha\n", "beta\n"]
    assert emitter.chunks == ["alpha\n", "beta\n"]


async def test_shell_emits_tool_exit_ok() -> None:
    ev = _EventEmitter()
    ctx = _ctx(_FakeRunner(), emit_event=ev)
    r = await ShellTool().execute(ShellArgs(command="echo x"), ctx)
    assert r.ok is True
    assert ev.events == [{"type": "tool.exit", "exit_code": 0, "ok": True}]


async def test_shell_emits_tool_exit_failure() -> None:
    ev = _EventEmitter()
    ctx = _ctx(_FakeRunner(exit_code=3), emit_event=ev)
    r = await ShellTool().execute(ShellArgs(command=_FAIL_CMD), ctx)
    assert r.ok is False
    assert ev.events == [{"type": "tool.exit", "exit_code": 3, "ok": False}]


async def test_shell_no_emit_assemblies_skipped() -> None:
    """emit_stdout / emit_event 均未装配：安全 no-op，行为与 17 完全一致。"""
    runner = _FakeRunner()
    r = await ShellTool().execute(ShellArgs(command="echo x"), _ctx(runner))
    assert r.ok is True
    assert runner.on_stdout_calls == []


async def test_shell_streams_with_real_sandbox(sandbox: Sandbox) -> None:
    """真实沙箱端到端：行级回调 + tool.exit 事件。"""
    emitter = _StdoutEmitter()
    ev = _EventEmitter()
    ctx = _ctx(sandbox, emit_stdout=emitter, emit_event=ev)
    multi = "(echo a&echo b)" if IS_WIN else "echo a; echo b"
    r = await ShellTool().execute(ShellArgs(command=multi), ctx)
    assert r.ok is True
    assert [c.rstrip("\r\n") for c in emitter.chunks] == ["a", "b"]
    assert ev.events == [{"type": "tool.exit", "exit_code": 0, "ok": True}]
