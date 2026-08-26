"""Task 15 ToolRegistry / ToolSpec 的单元测试。"""

import pytest
from pydantic import BaseModel, ConfigDict

from app.tools.base import ExecContext, Tool, ToolNotFoundError, ToolResult
from app.tools.registry import ToolRegistry, ToolSpec


class ArgsA(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str


class ArgsB(BaseModel):
    model_config = ConfigDict(extra="forbid")

    count: int


class ToolA(Tool[ArgsA]):
    name = "fake_a"
    description = "Fake tool A"
    args_schema = ArgsA

    async def execute(self, args: ArgsA, ctx: ExecContext) -> ToolResult:
        return ToolResult(ok=True, output=args.text)


class ToolB(Tool[ArgsB]):
    name = "fake_b"
    description = "Fake tool B"
    args_schema = ArgsB

    async def execute(self, args: ArgsB, ctx: ExecContext) -> ToolResult:
        return ToolResult(ok=True, output=str(args.count))


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


# ===== R1-R3: register / get =====


def test_register_get_roundtrip(registry: ToolRegistry) -> None:
    tool = ToolA()
    registry.register(tool)
    assert registry.get("fake_a") is tool


def test_duplicate_name_raises(registry: ToolRegistry) -> None:
    registry.register(ToolA())
    with pytest.raises(ValueError):
        registry.register(ToolA())


def test_get_unknown_raises(registry: ToolRegistry) -> None:
    with pytest.raises(ToolNotFoundError):
        registry.get("nope")


# ===== R4: list_names =====


def test_list_names_in_registration_order(registry: ToolRegistry) -> None:
    registry.register(ToolA())
    registry.register(ToolB())
    assert registry.list_names() == ["fake_a", "fake_b"]


# ===== R5-R6: to_specs =====


def test_to_specs_content(registry: ToolRegistry) -> None:
    tool = ToolA()
    registry.register(tool)
    specs = registry.to_specs()
    assert len(specs) == 1
    spec = specs[0]
    assert isinstance(spec, ToolSpec)
    assert spec.name == "fake_a"
    assert spec.description == "Fake tool A"
    assert spec.parameters == tool.json_schema


def test_to_specs_order(registry: ToolRegistry) -> None:
    registry.register(ToolA())
    registry.register(ToolB())
    assert [s.name for s in registry.to_specs()] == ["fake_a", "fake_b"]


# ===== R7: 空注册表 =====


def test_empty_registry(registry: ToolRegistry) -> None:
    assert registry.list_names() == []
    assert registry.to_specs() == []


# ===== R8: 泛型工具注册 =====


def test_generic_tool_registered_and_schema_usable(registry: ToolRegistry) -> None:
    tool = ToolB()
    registry.register(tool)
    fetched = registry.get("fake_b")
    assert fetched.json_schema["type"] == "object"
    assert fetched.json_schema["properties"]["count"]["type"] == "integer"
