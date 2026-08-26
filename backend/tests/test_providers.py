"""Provider Plugin Registry 测试。"""

from typing import cast
from uuid import uuid4

import pytest
from instructor import AsyncInstructor

from app.llm.providers.base import PROVIDER_REGISTRY, ProviderSpec, register_provider
from app.llm.types import ResolvedAdapter


def test_builtin_providers_registered() -> None:
    """T1：内置 anthropic / openai / openai_compatible 三个 spec 在 registry 中。"""
    assert "anthropic" in PROVIDER_REGISTRY
    assert "openai" in PROVIDER_REGISTRY
    assert "openai_compatible" in PROVIDER_REGISTRY
    assert len(PROVIDER_REGISTRY) >= 3


def test_duplicate_register_raises_value_error() -> None:
    """T2：重复注册同 key 抛 ValueError。"""
    with pytest.raises(ValueError, match="already registered"):

        @register_provider
        def _dup() -> ProviderSpec:
            return ProviderSpec(
                key="anthropic",
                label="dup",
                credentials_fields={},
                build_client=lambda r: cast(AsyncInstructor, None),
            )


def test_mock_provider_build_client_called() -> None:
    """T3：通过 registry 取出 spec 后 build_client 可被调用。"""
    spec = PROVIDER_REGISTRY["anthropic"]
    adapter = ResolvedAdapter(
        id=uuid4(),
        name="test",
        provider="anthropic",
        model="claude-sonnet-4-6",
        credentials={"api_key": "sk-test"},
        params={},
    )
    client = spec.build_client(adapter)
    assert client is not None


def test_openai_compatible_validate_config_missing_base_url() -> None:
    """T4：openai_compatible validate_config 缺 base_url 抛异常。"""
    spec = PROVIDER_REGISTRY["openai_compatible"]
    with pytest.raises(ValueError, match="base_url required"):
        spec.validate_config({"params": {}})


def test_openai_compatible_validate_config_with_base_url() -> None:
    """T5：openai_compatible validate_config 含 base_url 通过。"""
    spec = PROVIDER_REGISTRY["openai_compatible"]
    spec.validate_config({"params": {"base_url": "https://api.deepseek.com/v1"}})
