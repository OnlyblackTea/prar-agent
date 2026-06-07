"""Comment API 数据契约。"""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class CommentCreate(BaseModel):
    """前端写入评论请求体。"""

    anchor_id: str = Field(min_length=1, max_length=64)
    plan_version: int = Field(ge=1)
    quote: str = Field(min_length=1, max_length=2000)
    quote_context: str = Field(default="", max_length=200)
    body: str = Field(min_length=1, max_length=4000)


class CommentResponse(BaseModel):
    """评论返回体。"""

    id: UUID
    session_id: UUID
    plan_version: int
    anchor_id: str
    quote: str
    quote_context: str
    body: str
    resolved: bool
    created_at: datetime

    model_config = {"from_attributes": True}
