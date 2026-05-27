from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import instructor

from app.llm.types import ResolvedAdapter

FieldType = Literal["text", "url", "secret_env_name", "select"]


@dataclass(frozen=True, slots=True)
class FieldDef:
    """Provider 自定义字段的 wizard 元数据。"""

    label: str
    type: FieldType
    required: bool = True
    placeholder: str = ""
    validate_pattern: str | None = None
    options: list[str] | None = None


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    """Contributor 实现这个 spec 即注入新 provider，无需改框架核心。"""

    key: str
    label: str
    credentials_fields: dict[str, FieldDef]
    build_client: Callable[[ResolvedAdapter], instructor.AsyncInstructor] = field(repr=False)
    params_fields: dict[str, FieldDef] = field(default_factory=dict)
    validate_config: Callable[[dict[str, Any]], None] = field(
        default=lambda _: None, repr=False
    )


PROVIDER_REGISTRY: dict[str, ProviderSpec] = {}


def register_provider(
    spec_factory: Callable[[], ProviderSpec],
) -> Callable[[], ProviderSpec]:
    """Decorator：把工厂函数生成的 spec 注册到全局 registry。"""
    spec = spec_factory()
    if spec.key in PROVIDER_REGISTRY:
        raise ValueError(f"Provider {spec.key!r} already registered")
    PROVIDER_REGISTRY[spec.key] = spec
    return spec_factory
