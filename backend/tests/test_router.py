"""LLMRouter 单元测试（4.1b 重写：ResolvedAdapter + Provider Registry）。

策略：mock `instructor.AsyncInstructor` 客户端，不真调任何 API。
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from instructor.core import InstructorRetryException
from pydantic import BaseModel

from app.config import Settings
from app.llm.providers.base import PROVIDER_REGISTRY, FieldDef, ProviderSpec
from app.llm.router import (
    LLMRouter,
    LLMTransportError,
    StructuredOutputError,
    StructuredResponse,
)
from app.llm.types import ResolvedAdapter

# ===== Test schema =====


class FooSchema(BaseModel):
    name: str
    value: int = 0


# ===== Mock SDK 响应对象 =====


class _AnthropicUsage:
    def __init__(self, input_tokens: int = 10, output_tokens: int = 20) -> None:
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens


class _AnthropicMessage:
    def __init__(
        self,
        usage: _AnthropicUsage | None = None,
        stop_reason: str = "end_turn",
    ) -> None:
        self.usage = usage if usage is not None else _AnthropicUsage()
        self.stop_reason = stop_reason


class _OpenAIUsage:
    def __init__(
        self,
        prompt_tokens: int = 15,
        completion_tokens: int = 25,
        total_tokens: int = 40,
    ) -> None:
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens


class _OpenAIChoice:
    def __init__(self, finish_reason: str = "stop") -> None:
        self.finish_reason = finish_reason


class _OpenAICompletion:
    def __init__(
        self,
        usage: _OpenAIUsage | None = None,
        finish_reason: str = "stop",
    ) -> None:
        self.usage = usage if usage is not None else _OpenAIUsage()
        self.choices = [_OpenAIChoice(finish_reason=finish_reason)]


# ===== Helper: 构建 ResolvedAdapter =====


def _adapter(
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    credentials: dict[str, str] | None = None,
    params: dict[str, str] | None = None,
) -> ResolvedAdapter:
    return ResolvedAdapter(
        id=uuid4(),
        name="test-adapter",
        provider=provider,
        model=model,
        credentials=credentials or {"api_key": "sk-test"},
        params=params or {},
    )


# ===== Fixtures =====


@pytest.fixture
def settings() -> Settings:
    return Settings()


@pytest.fixture
def router(settings: Settings) -> LLMRouter:
    return LLMRouter(settings)


def _inject_mock_client(router: LLMRouter, adapter: ResolvedAdapter) -> MagicMock:
    """向 router 缓存中注入 mock 客户端，避免真实 SDK 初始化。"""
    mock = MagicMock()
    mock.chat.completions.create_with_completion = AsyncMock()
    cache_key = (
        adapter.provider,
        frozenset(adapter.credentials.items()),
        frozenset(adapter.params.items()),
    )
    router._client_cache[cache_key] = mock
    return mock


# ===== T1-T2: 路由到正确 provider =====


async def test_routes_anthropic_adapter(router: LLMRouter) -> None:
    adapter = _adapter(provider="anthropic")
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="hi")
    mock.chat.completions.create_with_completion.return_value = (foo, _AnthropicMessage())

    result = await router.complete_structured(
        adapter=adapter, system="sys", user="usr", schema=FooSchema
    )
    assert result.parsed.name == "hi"
    mock.chat.completions.create_with_completion.assert_called_once()


async def test_routes_openai_adapter(router: LLMRouter) -> None:
    adapter = _adapter(provider="openai", model="gpt-5")
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="hi")
    mock.chat.completions.create_with_completion.return_value = (foo, _OpenAICompletion())

    result = await router.complete_structured(
        adapter=adapter, system="sys", user="usr", schema=FooSchema
    )
    assert result.parsed.name == "hi"
    mock.chat.completions.create_with_completion.assert_called_once()


# ===== T3: structured response =====


async def test_returns_structured_response(router: LLMRouter) -> None:
    adapter = _adapter()
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="alice", value=42)
    mock.chat.completions.create_with_completion.return_value = (foo, _AnthropicMessage())

    result = await router.complete_structured(
        adapter=adapter, system="s", user="u", schema=FooSchema
    )
    assert isinstance(result, StructuredResponse)
    assert isinstance(result.parsed, FooSchema)
    assert result.parsed.name == "alice"
    assert result.model_id == adapter.model


# ===== T4: InstructorRetryException → StructuredOutputError =====


async def test_instructor_retry_becomes_structured_output_error(router: LLMRouter) -> None:
    adapter = _adapter()
    mock = _inject_mock_client(router, adapter)
    exc = InstructorRetryException.__new__(InstructorRetryException)
    Exception.__init__(exc, "validation failed")
    exc.last_completion = "bad output"
    mock.chat.completions.create_with_completion.side_effect = exc

    with pytest.raises(StructuredOutputError) as exc_info:
        await router.complete_structured(
            adapter=adapter, system="s", user="u", schema=FooSchema
        )
    assert exc_info.value.model_id == adapter.model
    assert "bad output" in (exc_info.value.raw_text or "")


# ===== T5: arbitrary exception → LLMTransportError =====


async def test_arbitrary_exception_wrapped_as_transport_error(router: LLMRouter) -> None:
    adapter = _adapter()
    mock = _inject_mock_client(router, adapter)
    cause = RuntimeError("boom")
    mock.chat.completions.create_with_completion.side_effect = cause

    with pytest.raises(LLMTransportError) as exc_info:
        await router.complete_structured(
            adapter=adapter, system="s", user="u", schema=FooSchema
        )
    assert exc_info.value.cause is cause


# ===== T6-T7: usage extraction =====


async def test_extracts_anthropic_usage(router: LLMRouter) -> None:
    adapter = _adapter()
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="x")
    mock.chat.completions.create_with_completion.return_value = (
        foo, _AnthropicMessage(usage=_AnthropicUsage(input_tokens=10, output_tokens=20))
    )

    result = await router.complete_structured(
        adapter=adapter, system="s", user="u", schema=FooSchema
    )
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 20
    assert result.usage.total_tokens == 30


async def test_extracts_openai_usage(router: LLMRouter) -> None:
    adapter = _adapter(provider="openai", model="gpt-5")
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="x")
    mock.chat.completions.create_with_completion.return_value = (
        foo, _OpenAICompletion(usage=_OpenAIUsage(15, 25, 40))
    )

    result = await router.complete_structured(
        adapter=adapter, system="s", user="u", schema=FooSchema
    )
    assert result.usage.input_tokens == 15
    assert result.usage.output_tokens == 25
    assert result.usage.total_tokens == 40


# ===== T8-T9: finish_reason extraction =====


async def test_extracts_anthropic_finish_reason(router: LLMRouter) -> None:
    adapter = _adapter()
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="x")
    mock.chat.completions.create_with_completion.return_value = (
        foo, _AnthropicMessage(stop_reason="end_turn")
    )

    result = await router.complete_structured(
        adapter=adapter, system="s", user="u", schema=FooSchema
    )
    assert result.finish_reason == "end_turn"


async def test_extracts_openai_finish_reason(router: LLMRouter) -> None:
    adapter = _adapter(provider="openai", model="gpt-5")
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="x")
    mock.chat.completions.create_with_completion.return_value = (
        foo, _OpenAICompletion(finish_reason="stop")
    )

    result = await router.complete_structured(
        adapter=adapter, system="s", user="u", schema=FooSchema
    )
    assert result.finish_reason == "stop"


# ===== T10: kwargs passthrough =====


async def test_passes_max_retries_temperature_max_tokens(router: LLMRouter) -> None:
    adapter = _adapter()
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="x")
    mock.chat.completions.create_with_completion.return_value = (foo, _AnthropicMessage())

    await router.complete_structured(
        adapter=adapter,
        system="s",
        user="u",
        schema=FooSchema,
        max_retries=5,
        temperature=0.3,
        max_tokens=2048,
    )

    call = mock.chat.completions.create_with_completion.call_args
    assert call.kwargs["max_retries"] == 5
    assert call.kwargs["temperature"] == 0.3
    assert call.kwargs["max_tokens"] == 2048


# ===== T11: raw_text == parsed.model_dump_json() =====


async def test_raw_text_is_parsed_model_dump_json(router: LLMRouter) -> None:
    adapter = _adapter()
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="bob", value=7)
    mock.chat.completions.create_with_completion.return_value = (foo, _AnthropicMessage())

    result = await router.complete_structured(
        adapter=adapter, system="s", user="u", schema=FooSchema
    )
    assert result.raw_text == foo.model_dump_json()


# ===== T15: unknown provider → LLMTransportError =====


async def test_unknown_provider_raises_transport_error(router: LLMRouter) -> None:
    adapter = _adapter(provider="nonexistent", model="some-model")

    with pytest.raises(LLMTransportError) as exc_info:
        await router.complete_structured(
            adapter=adapter, system="s", user="u", schema=FooSchema
        )
    assert "Unknown provider" in str(exc_info.value)
    assert exc_info.value.model_id == "some-model"


# ===== T16: 客户端缓存复用 =====


async def test_client_cache_reuse(router: LLMRouter) -> None:
    adapter = _adapter()
    mock = _inject_mock_client(router, adapter)
    foo = FooSchema(name="x")
    mock.chat.completions.create_with_completion.return_value = (foo, _AnthropicMessage())

    await router.complete_structured(adapter=adapter, system="s", user="u", schema=FooSchema)
    await router.complete_structured(adapter=adapter, system="s", user="u", schema=FooSchema)

    assert len(router._client_cache) == 1
    assert mock.chat.completions.create_with_completion.call_count == 2


# ===== T17: 不同 credentials 不复用客户端 =====


async def test_different_credentials_different_client(router: LLMRouter) -> None:
    adapter_a = _adapter(credentials={"api_key": "key-a"})
    adapter_b = _adapter(credentials={"api_key": "key-b"})
    mock_a = _inject_mock_client(router, adapter_a)
    mock_b = _inject_mock_client(router, adapter_b)
    foo = FooSchema(name="x")
    mock_a.chat.completions.create_with_completion.return_value = (foo, _AnthropicMessage())
    mock_b.chat.completions.create_with_completion.return_value = (foo, _AnthropicMessage())

    await router.complete_structured(adapter=adapter_a, system="s", user="u", schema=FooSchema)
    await router.complete_structured(adapter=adapter_b, system="s", user="u", schema=FooSchema)

    assert len(router._client_cache) == 2
    mock_a.chat.completions.create_with_completion.assert_called_once()
    mock_b.chat.completions.create_with_completion.assert_called_once()


# ===== T18: mock provider 通过 registry 分发 =====


async def test_mock_provider_dispatches_via_registry(router: LLMRouter) -> None:
    """注入临时 mock provider 到 registry，验证 router 能正确分发。"""
    mock_client = MagicMock()
    mock_client.chat.completions.create_with_completion = AsyncMock()
    foo = FooSchema(name="from-mock")
    mock_client.chat.completions.create_with_completion.return_value = (
        foo, _AnthropicMessage()
    )

    test_key = f"_test_provider_{uuid4().hex[:8]}"
    PROVIDER_REGISTRY[test_key] = ProviderSpec(
        key=test_key,
        label="Test Provider",
        credentials_fields={
            "token": FieldDef(label="Token Env", type="secret_env_name"),
        },
        build_client=lambda _r: mock_client,
    )
    try:
        adapter = _adapter(provider=test_key, credentials={"token": "test-val"})
        result = await router.complete_structured(
            adapter=adapter, system="s", user="u", schema=FooSchema
        )
        assert result.parsed.name == "from-mock"
        mock_client.chat.completions.create_with_completion.assert_called_once()
    finally:
        del PROVIDER_REGISTRY[test_key]
