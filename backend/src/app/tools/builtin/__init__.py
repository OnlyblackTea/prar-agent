"""Task 17 内置工具工厂：Task 18 dispatcher 循环注册 builtin_tools()。"""

from typing import Any

from app.tools.base import Tool
from app.tools.builtin.fs import FsReadTool, FsWriteTool
from app.tools.builtin.shell import ShellTool


def builtin_tools() -> list[Tool[Any]]:
    """返回三个内置工具实例（固定顺序：shell / fs.read / fs.write）。"""
    return [ShellTool(), FsReadTool(), FsWriteTool()]


__all__ = ["builtin_tools"]
