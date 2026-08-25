"""Review Merger LLM structured output schema。"""
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.plan_schemas import CriticAction


class MergerAction(BaseModel):
    """对单条用户评论的处理结果。"""

    comment_id: UUID = Field(description="对应 comments.id")
    decision: Literal["accept", "reject", "partial"]
    reason: str = Field(min_length=1, description="一句话解释为何此决策")
    # decision == reject 时 patch 必须 None；
    # decision ∈ {accept, partial} 时 patch 应包含具体的 plan 修改动作
    patch: CriticAction | None = None


class MergerResult(BaseModel):
    """ReviewMerger LLM 输出。"""

    actions: list[MergerAction] = Field(default_factory=list)
    overall_comment: str = Field(default="", description="对整体修订的总评")
