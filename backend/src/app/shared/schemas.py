"""Shared schema registry — 所有需要导出到前端的 pydantic 模型在此注册.

WARNING: 本模块只从业务模块导入 pydantic 模型类, 不反向暴露给它们,
避免循环导入。若未来业务模块需要引用 shared 中的定义, 应把该定义上提
到 shared 或单独的基础模块.
"""

from app.api.adapters import (
    ModelAdapterCreate,
    ModelAdapterResponse,
    ModelAdapterUpdate,
)
from app.health import HealthResponse

SHARED_SCHEMAS: list[type] = [
    HealthResponse,
    ModelAdapterCreate,
    ModelAdapterUpdate,
    ModelAdapterResponse,
]
