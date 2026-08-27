"""Tool 层公开契约：ABC / 数据类 / 协议 / 注册表 / 沙箱 / 异常。"""

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
from app.tools.sandbox import Sandbox, SandboxLimits

__all__ = [
    "ExecContext",
    "Sandbox",
    "SandboxLimits",
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
