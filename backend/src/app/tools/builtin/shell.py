"""Task 17 内置工具 shell：在沙箱隔离工作目录中执行系统 shell 命令。"""

import sys
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
    - 不流式：Sandbox.run 是全量返回接口，流式管道是 Task 19 的交付
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
        r = await ctx.run_shell.run(argv, timeout=args.timeout, cwd=ctx.workdir)
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
