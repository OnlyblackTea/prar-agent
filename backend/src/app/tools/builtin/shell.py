"""Task 17 内置工具 shell：在沙箱隔离工作目录中执行系统 shell 命令。"""

import sys
from collections.abc import Awaitable, Callable
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ExecContext, Tool, ToolResult

IS_WIN = sys.platform == "win32"


class ShellArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(description="要在隔离工作目录中执行的 shell 命令")
    timeout: float | None = Field(
        default=None, description="超时秒数；None 用沙箱默认 300s"
    )


class ShellTool(Tool[ShellArgs]):
    """执行 shell 命令：Windows `cmd /c`；POSIX `/bin/sh -c`。

    - exit_code==0 → ok=True；≠0（含 124 超时）→ ok=False（业务失败，LLM 可重试）
    - output 固定三段格式（exit_code / stdout / stderr，stderr 空标 (empty)）
    - Task 19 流式：stdout 行级经 emit_stdout 转发；结束时经 emit_event 发 tool.exit
    """

    name: ClassVar[str] = "shell"
    description: ClassVar[str] = (
        "在沙箱隔离工作目录中执行 shell 命令，返回 exit_code / stdout / stderr。"
    )
    args_schema: ClassVar[type[BaseModel]] = ShellArgs
    rerunnable: ClassVar[bool] = False

    async def execute(self, args: ShellArgs, ctx: ExecContext) -> ToolResult:
        argv = (
            ["cmd", "/d", "/s", "/c", args.command]
            if IS_WIN
            else ["/bin/sh", "-c", args.command]
        )
        r = await ctx.run_shell.run(
            argv,
            timeout=args.timeout,
            cwd=ctx.workdir,
            on_stdout=self._stdout_cb(ctx),
        )
        if ctx.emit_event is not None:
            await ctx.emit_event.emit(
                {
                    "type": "tool.exit",
                    "exit_code": r.exit_code,
                    "ok": r.exit_code == 0,
                }
            )
        stdout = r.stdout.strip()
        stderr = r.stderr.strip()
        return ToolResult(
            ok=r.exit_code == 0,
            output=(
                f"exit_code={r.exit_code}\n"
                f"stdout:\n{stdout}\n"
                f"stderr:\n{stderr or '(empty)'}"
            ),
        )

    def _stdout_cb(
        self, ctx: ExecContext
    ) -> Callable[[str], Awaitable[None]] | None:
        if ctx.emit_stdout is None:
            return None
        return lambda chunk: self._emit(ctx, chunk)
