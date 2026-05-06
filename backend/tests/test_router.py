"""Task 04 LLMRouter 单元测试。

策略：mock `instructor.AsyncInstructor` 客户端（绕过 lazy-init），不真调任何 API。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from instructor.core import InstructorRetryException
from pydantic import BaseModel

from app.config import Settings
from app.llm.router import (
    LLMRouter,
    LLMTransportError,
    StructuredOutputError,
    StructuredResponse,
)

# ===== Test schema =====


class FooSchema(BaseModel):
    name: str
    value: int = 0


# ===== Mock helpers（最小字段表面，模拟两家 SDK 的响应对象）=====


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


# ===== Fixtures =====


@pytest.fixture
def settings() -> Settings:
    """显式注入 default_model_id 为 claude- 前缀，T5 测试默认 fallback 时可控。"""
    return Settings(default_model_id="claude-sonnet-4-6")


@pytest.fixture
def router(settings: Settings) -> LLMRouter:
    return LLMRouter(settings)


@pytest.fixture
def mock_anthropic(router: LLMRouter) -> MagicMock:
    mock = MagicMock()
    mock.chat.completions.create_with_completion = AsyncMock()
    router._anthropic = mock
    return mock


@pytest.fixture
def mock_openai(router: LLMRouter) -> MagicMock:
    mock = MagicMock()
    mock.chat.completions.create_with_completion = AsyncMock()
    router._openai = mock
    return mock


# ===== T1-T3: 路由前缀分流 =====


async def test_routes_claude_to_anthropic_client(
    router: LLMRouter, mock_anthropic: MagicMock
) -> None:
    foo = FooSchema(name="hi")
    mock_anthropic.chat.completions.create_with_completion.return_value = (
        foo,
        _AnthropicMessage(),
    )

    result = await router.complete_structured(
        model_id="claude-sonnet-4-6",
        system="sys",
        user="usr",
        schema=FooSchema,
    )

    assert result.parsed.name == "hi"
    mock_anthropic.chat.completions.create_with_completion.assert_called_once()
    assert router._openai is None


async def test_routes_gpt_to_openai_client(
    router: LLMRouter, mock_openai: MagicMock
) -> None:
    foo = FooSchema(name="hi")
    mock_openai.chat.completions.create_with_completion.return_value = (
        foo,
        _OpenAICompletion(),
    )

    result = await router.complete_structured(
        model_id="gpt-5",
        system="sys",
        user="usr",
        schema=FooSchema,
    )

    assert result.parsed.name == "hi"
    mock_openai.chat.completions.create_with_completion.assert_called_once()
    assert router._anthropic is None


@pytest.mark.parametrize("model_id", ["o1-mini", "o3-large", "openai/gpt-5"])
async def test_routes_o1_o3_openai_prefix_to_openai(
    router: LLMRouter, mock_openai: MagicMock, model_id: str
) -> None:
    foo = FooSchema(name="x")
    mock_openai.chat.completions.create_with_completion.return_value = (
        foo,
        _OpenAICompletion(),
    )

    await router.complete_structured(
        model_id=model_id, system="s", user="u", schema=FooSchema
    )
    mock_openai.chat.completions.create_with_completion.assert_called_once()


# ===== T4: unknown model =====


async def test_unknown_model_id_raises_transport_error(
    router: LLMRouter,
) -> None:
    with pytest.raises(LLMTransportError) as exc_info:
        await router.complete_structured(
            model_id="qwen-turbo",
            system="s",
            user="u",
            schema=FooSchema,
        )
    assert exc_info.value.model_id == "qwen-turbo"
    assert "Unsupported" in str(exc_info.value)


# ===== T5: default model fallback =====


async def test_uses_default_model_when_id_none(
    router: LLMRouter, settings: Settings, mock_anthropic: MagicMock
) -> None:
    foo = FooSchema(name="x")
    mock_anthropic.chat.completions.create_with_completion.return_value = (
        foo,
        _AnthropicMessage(),
    )

    await router.complete_structured(
        model_id=None, system="s", user="u", schema=FooSchema
    )

    call = mock_anthropic.chat.completions.create_with_completion.call_args
    assert call.kwargs["model"] == settings.default_model_id


# ===== T6: structured response =====


async def test_returns_structured_response_with_parsed_pydantic(
    router: LLMRouter, mock_anthropic: MagicMock
) -> None:
    foo = FooSchema(name="alice", value=42)
    mock_anthropic.chat.completions.create_with_completion.return_value = (
        foo,
        _AnthropicMessage(),
    )

    result = await router.complete_structured(
        model_id="claude-sonnet-4-6",
        system="s",
        user="u",
        schema=FooSchema,
    )

    assert isinstance(result, StructuredResponse)
    assert isinstance(result.parsed, FooSchema)
    assert result.parsed.name == "alice"
    assert result.parsed.value == 42
    assert result.model_id == "claude-sonnet-4-6"


# ===== T7: InstructorRetryException → StructuredOutputError =====


async def test_instructor_retry_exception_becomes_structured_output_error(
    router: LLMRouter, mock_anthropic: MagicMock
) -> None:
    # 用 __new__ 绕开 InstructorRetryException 复杂构造签名（版本可能变）
    exc = InstructorRetryException.__new__(InstructorRetryException)
    Exception.__init__(exc, "validation failed after retries")
    exc.last_completion = "bad raw output"

    mock_anthropic.chat.completions.create_with_completion.side_effect = exc

    with pytest.raises(StructuredOutputError) as exc_info:
        await router.complete_structured(
            model_id="claude-sonnet-4-6",
            system="s",
            user="u",
            schema=FooSchema,
        )

    assert exc_info.value.model_id == "claude-sonnet-4-6"
    assert "bad raw output" in (exc_info.value.raw_text or "")


# ===== T8: arbitrary exception → LLMTransportError =====


async def test_arbitrary_exception_wrapped_as_transport_error(
    router: LLMRouter, mock_anthropic: MagicMock
) -> None:
    cause = RuntimeError("boom")
    mock_anthropic.chat.completions.create_with_completion.side_effect = cause

    with pytest.raises(LLMTransportError) as exc_info:
        await router.complete_structured(
            model_id="claude-sonnet-4-6",
            system="s",
            user="u",
            schema=FooSchema,
        )

    assert exc_info.value.model_id == "claude-sonnet-4-6"
    assert exc_info.value.cause is cause
    assert "boom" in str(exc_info.value)


# ===== T9-T10: usage extraction =====


async def test_extracts_anthropic_usage(
    router: LLMRouter, mock_anthropic: MagicMock
) -> None:
    foo = FooSchema(name="x")
    mock_anthropic.chat.completions.create_with_completion.return_value = (
        foo,
        _AnthropicMessage(usage=_AnthropicUsage(input_tokens=10, output_tokens=20)),
    )

    result = await router.complete_structured(
        model_id="claude-sonnet-4-6",
        system="s",
        user="u",
        schema=FooSchema,
    )

    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 20
    assert result.usage.total_tokens == 30


async def test_extracts_openai_usage(
    router: LLMRouter, mock_openai: MagicMock
) -> None:
    foo = FooSchema(name="x")
    mock_openai.chat.completions.create_with_completion.return_value = (
        foo,
        _OpenAICompletion(
            usage=_OpenAIUsage(
                prompt_tokens=15, completion_tokens=25, total_tokens=40
            )
        ),
    )

    result = await router.complete_structured(
        model_id="gpt-5", system="s", user="u", schema=FooSchema
    )

    assert result.usage.input_tokens == 15
    assert result.usage.output_tokens == 25
    assert result.usage.total_tokens == 40


# ===== T11-T12: finish_reason extraction =====


async def test_extracts_anthropic_finish_reason(
    router: LLMRouter, mock_anthropic: MagicMock
) -> None:
    foo = FooSchema(name="x")
    mock_anthropic.chat.completions.create_with_completion.return_value = (
        foo,
        _AnthropicMessage(stop_reason="end_turn"),
    )

    result = await router.complete_structured(
        model_id="claude-sonnet-4-6",
        system="s",
        user="u",
        schema=FooSchema,
    )

    assert result.finish_reason == "end_turn"


async def test_extracts_openai_finish_reason(
    router: LLMRouter, mock_openai: MagicMock
) -> None:
    foo = FooSchema(name="x")
    mock_openai.chat.completions.create_with_completion.return_value = (
        foo,
        _OpenAICompletion(finish_reason="stop"),
    )

    result = await router.complete_structured(
        model_id="gpt-5", system="s", user="u", schema=FooSchema
    )

    assert result.finish_reason == "stop"


# ===== T13: kwargs passthrough =====


async def test_passes_max_retries_temperature_max_tokens(
    router: LLMRouter, mock_anthropic: MagicMock
) -> None:
    foo = FooSchema(name="x")
    mock_anthropic.chat.completions.create_with_completion.return_value = (
        foo,
        _AnthropicMessage(),
    )

    await router.complete_structured(
        model_id="claude-sonnet-4-6",
        system="s",
        user="u",
        schema=FooSchema,
        max_retries=5,
        temperature=0.3,
        max_tokens=2048,
    )

    call = mock_anthropic.chat.completions.create_with_completion.call_args
    assert call.kwargs["max_retries"] == 5
    assert call.kwargs["temperature"] == 0.3
    assert call.kwargs["max_tokens"] == 2048


# ===== T14: raw_text == parsed.model_dump_json() =====


async def test_raw_text_is_parsed_model_dump_json(
    router: LLMRouter, mock_anthropic: MagicMock
) -> None:
    foo = FooSchema(name="bob", value=7)
    mock_anthropic.chat.completions.create_with_completion.return_value = (
        foo,
        _AnthropicMessage(),
    )

    result = await router.complete_structured(
        model_id="claude-sonnet-4-6",
        system="s",
        user="u",
        schema=FooSchema,
    )

    assert result.raw_text == foo.model_dump_json()
