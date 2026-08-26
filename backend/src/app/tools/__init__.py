"""Tool 层公开契约：ABC / 数据类 / 协议 / 注册表 / 异常。"""

from app.tools.base import (
    ExecContext,
    ShellResult,
    ShellRunner,
    StdoutEmitter,
    Tool,
    ToolError,
    ToolExecutionError,
    ToolNotFoundError,
    ToolResult,
    ToolValidationError,
)
from app.tools.registry import ToolRegistry, ToolSpec

__all__ = [
    "ExecContext",
    "ShellResult",
    "ShellRunner",
    "StdoutEmitter",
    "Tool",
    "ToolError",
    "ToolExecutionError",
    "ToolNotFoundError",
    "ToolRegistry",
    "ToolResult",
    "ToolSpec",
    "ToolValidationError",
]
