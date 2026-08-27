"""Task 17 内置工具 fs.read / fs.write：沙箱 workdir 内的文件读写。

路径语义（与 sandbox 一致）：
- `ExecContext.workdir` 是沙箱视角相对路径；fs 工具直接依赖 `Sandbox`
  （run_shell 必须是 Sandbox，Task 18 dispatcher 恒装配 Sandbox）取得宿主沙箱根
- workdir 自身逃出沙箱根 → ToolExecutionError（dispatcher 装配错误，环境故障）
- `path` 绝对 / 含 `..` / resolve 后逃出 workdir → ToolResult(ok=False)（LLM 输入，业务失败）
"""

import os
from pathlib import Path
from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ExecContext, Tool, ToolExecutionError, ToolResult
from app.tools.sandbox import Sandbox


def _host_workdir(ctx: ExecContext) -> Path:
    """把沙箱视角 workdir 解析为宿主绝对路径；非法 → ToolExecutionError。"""
    if not isinstance(ctx.run_shell, Sandbox):
        raise ToolExecutionError(
            f"fs tools require Sandbox as run_shell, got {type(ctx.run_shell).__name__}"
        )
    root = ctx.run_shell.root
    if ctx.workdir.is_absolute():
        raise ToolExecutionError(f"workdir must be relative to sandbox root: {ctx.workdir}")
    full = (root / ctx.workdir).resolve()
    if not full.is_relative_to(root):
        raise ToolExecutionError(f"workdir escapes sandbox root: {ctx.workdir}")
    return full


def _resolve_in_workdir(wd: Path, rel: str) -> Path | None:
    """把相对路径解析到 workdir 内；绝对 / 逃逸返回 None。"""
    p = Path(rel)
    if p.is_absolute():
        return None
    full = (wd / p).resolve()
    if not full.is_relative_to(wd):
        return None
    return full


class FsReadArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="相对工作目录的文件路径")
    max_bytes: int = Field(
        default=262144, ge=1024, le=1048576, description="最大读取字节数（1KB..1MB）"
    )


class FsReadTool(Tool[FsReadArgs]):
    """读 workdir 内文本文件（UTF-8）。

    - 缺失 / 目录 / 二进制 / 路径逃逸 → ok=False
    - 超 max_bytes → 截断并尾部注明 `[truncated: read N of M bytes]`
    - output 为文件内容原样（无路径前缀）
    """

    name: ClassVar[str] = "fs.read"
    description: ClassVar[str] = (
        "读取工作目录内的文本文件（UTF-8）。超过 max_bytes 截断并在尾部注明。"
    )
    args_schema: ClassVar[type[BaseModel]] = FsReadArgs

    async def execute(self, args: FsReadArgs, ctx: ExecContext) -> ToolResult:
        wd = _host_workdir(ctx)
        full = _resolve_in_workdir(wd, args.path)
        if full is None:
            return ToolResult(ok=False, output=f"path escapes workdir: {args.path}")
        if not full.exists():
            return ToolResult(ok=False, output=f"file not found: {args.path}")
        if full.is_dir():
            return ToolResult(ok=False, output=f"is a directory: {args.path}")
        try:
            with full.open("rb") as f:
                data = f.read(args.max_bytes + 1)
            total = full.stat().st_size
        except OSError as e:
            return ToolResult(ok=False, output=f"read failed: {e}")
        truncated = len(data) > args.max_bytes
        if truncated:
            data = data[: args.max_bytes]
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            if truncated:
                # 截断可能落在多字节字符边界：用 replace 解码（R5 对策）
                text = data.decode("utf-8", errors="replace")
            else:
                return ToolResult(ok=False, output=f"binary file: {args.path} ({total} bytes)")
        if truncated:
            text += f"\n[truncated: read {len(data)} of {total} bytes]"
        return ToolResult(ok=True, output=text)


class FsWriteArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(description="相对工作目录的文件路径（父目录自动创建）")
    content: str = Field(description="要写入的文本内容")


class FsWriteTool(Tool[FsWriteArgs]):
    """写 workdir 内文本文件：父目录自动创建、原子写（临时文件 + os.replace）。

    - artifacts 记录相对 workdir 的相对路径（Task 20 checkpoint 收集）
    """

    name: ClassVar[str] = "fs.write"
    description: ClassVar[str] = (
        "写入文本文件（UTF-8），父目录自动创建。返回写入字节数；文件记入 artifacts。"
    )
    args_schema: ClassVar[type[BaseModel]] = FsWriteArgs

    async def execute(self, args: FsWriteArgs, ctx: ExecContext) -> ToolResult:
        wd = _host_workdir(ctx)
        full = _resolve_in_workdir(wd, args.path)
        if full is None:
            return ToolResult(ok=False, output=f"path escapes workdir: {args.path}")
        nbytes = len(args.content.encode("utf-8"))
        tmp = full.with_name(f".{full.name}.tmp{os.getpid()}")
        try:
            full.parent.mkdir(parents=True, exist_ok=True)
            tmp.write_text(args.content, encoding="utf-8")
            os.replace(tmp, full)
        except OSError as e:
            return ToolResult(ok=False, output=f"write failed: {e}")
        return ToolResult(
            ok=True,
            output=f"wrote {nbytes} bytes to {args.path}",
            artifacts=[Path(args.path)],
        )
