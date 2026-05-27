from dataclasses import dataclass
from typing import Any
from uuid import UUID


@dataclass(frozen=True, slots=True)
class ResolvedAdapter:
    """运行时 adapter（credentials_env 已 resolve 为真实 secret 值）。"""

    id: UUID
    name: str
    provider: str
    model: str
    credentials: dict[str, str]
    params: dict[str, Any]
