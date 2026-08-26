"""工具注册表：注册/查询/枚举 + function-calling schema 生成。"""

from dataclasses import dataclass
from typing import Any

from app.tools.base import Tool, ToolNotFoundError


@dataclass(frozen=True, slots=True)
class ToolSpec:
    """喂给 LLM 的 function-calling 描述（标准 JSON Schema）。

    各 provider 的差异格式（OpenAI tools 数组 / Anthropic input_schema）
    由 Task 18/router 转换，本模块只产标准 JSON Schema。
    """

    name: str
    description: str
    parameters: dict[str, Any]


class ToolRegistry:
    """工具注册表：注册/查询/枚举。单实例，dispatcher 持有。"""

    def __init__(self) -> None:
        self._tools: dict[str, Tool[Any]] = {}

    def register(self, tool: Tool[Any]) -> None:
        """注册工具；name 重复 → ValueError（启动期配置错误，fail fast）。"""
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool[Any]:
        """按名查询；未命中 → ToolNotFoundError。"""
        try:
            return self._tools[name]
        except KeyError:
            raise ToolNotFoundError(f"Unknown tool: {name!r}") from None

    def list_names(self) -> list[str]:
        """按注册序返回全部工具名。"""
        return list(self._tools)

    def to_specs(self) -> list[ToolSpec]:
        """按注册序生成 function-calling schema 列表（Task 18 直接喂 LLM）。"""
        return [
            ToolSpec(name=t.name, description=t.description, parameters=t.json_schema)
            for t in self._tools.values()
        ]
