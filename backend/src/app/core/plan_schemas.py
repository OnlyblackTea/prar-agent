"""Plan 引擎的 Pydantic Schema — LLM structured output 目标。"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

# ===== Plan 节点类型 =====


class HeadingNode(BaseModel):
    type: Literal["heading"] = "heading"
    level: int = Field(ge=1, le=3)
    text: str


class ParagraphNode(BaseModel):
    type: Literal["paragraph"] = "paragraph"
    text: str


class DecisionNode(BaseModel):
    type: Literal["decision"] = "decision"
    id: str = Field(default="", description="框架后处理分配，LLM 必须留空")
    question: str
    kind: Literal["single_choice", "multi_choice"] = "single_choice"
    options: list[str] = Field(min_length=2, max_length=6)
    answer: str | None = None
    blocking: bool = True  # 第一轮硬编码 True


class GlossaryNode(BaseModel):
    type: Literal["glossary"] = "glossary"
    id: str = Field(default="", description="框架后处理分配，LLM 必须留空")
    term: str
    definition: str


class StepNode(BaseModel):
    type: Literal["step"] = "step"
    id: str = Field(default="", description="框架后处理分配，LLM 必须留空")
    title: str
    description: str
    tool: str  # Task 17 实现工具注册后补充校验
    tool_args: dict[str, Any] = Field(default_factory=dict)
    rerunnable: bool = True


PlanNode = Annotated[
    HeadingNode | ParagraphNode | DecisionNode | GlossaryNode | StepNode,
    Field(discriminator="type"),
]


class PlanDocument(BaseModel):
    """Planner LLM 输出的结构化计划。"""

    title: str
    summary: str
    nodes: list[PlanNode]


# ===== Critic 输出 =====


class CriticAction(BaseModel):
    """Critic 对单个节点的修正指令。"""

    node_index: int = Field(ge=0, description="目标节点在 nodes 列表中的下标")
    action: str  # "remove" | "replace" | "insert_after"
    reason: str
    replacement: PlanNode | None = None  # action=replace/insert_after 时提供


class CriticResult(BaseModel):
    """Critic 输出：一组修正 + 全局评语。"""

    actions: list[CriticAction] = Field(default_factory=list)
    overall_comment: str = ""
