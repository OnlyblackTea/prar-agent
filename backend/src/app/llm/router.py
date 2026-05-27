"""LLM 路由器：Provider Registry + Instructor 结构化输出。

公开 API：
    LLMRouter.complete_structured(...) -> StructuredResponse[T]

异常层级：
    LLMError
        ├── LLMTransportError      网络/认证/限流/unknown provider
        └── StructuredOutputError  Instructor 重试耗尽 / schema 不匹配
"""

from collections.abc import Hashable

import instructor
from instructor.core import InstructorRetryException
from pydantic import BaseModel, ConfigDict

from app.config import Settings
from app.llm.providers.base import PROVIDER_REGISTRY
from app.llm.types import ResolvedAdapter

# ===== 响应模型 =====


class TokenUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0


class StructuredResponse[T: BaseModel](BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    parsed: T
    raw_text: str
    model_id: str
    usage: TokenUsage
    finish_reason: str


# ===== 异常层级 =====


class LLMError(Exception):
    """所有 LLM 调用异常基类。"""


class LLMTransportError(LLMError):
    """网络 / 认证 / 限流 / unknown provider 等传输层异常。"""

    def __init__(
        self,
        message: str,
        *,
        model_id: str,
        cause: Exception | None = None,
    ) -> None:
        super().__init__(message)
        self.model_id = model_id
        self.cause = cause


class StructuredOutputError(LLMError):
    """模型在 max_retries 次内仍无法产出符合 schema 的 JSON。"""

    def __init__(
        self,
        message: str,
        *,
        model_id: str,
        raw_text: str | None = None,
    ) -> None:
        super().__init__(message)
        self.model_id = model_id
        self.raw_text = raw_text


# ===== 归一化辅助 =====


def _extract_usage(raw: object) -> TokenUsage:
    usage = getattr(raw, "usage", None)
    if usage is None:
        return TokenUsage()
    if hasattr(usage, "input_tokens"):
        i = int(getattr(usage, "input_tokens", 0) or 0)
        o = int(getattr(usage, "output_tokens", 0) or 0)
        return TokenUsage(input_tokens=i, output_tokens=o, total_tokens=i + o)
    if hasattr(usage, "prompt_tokens"):
        return TokenUsage(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        )
    return TokenUsage()


def _extract_finish_reason(raw: object) -> str:
    if hasattr(raw, "stop_reason"):
        return str(getattr(raw, "stop_reason", None) or "unknown")
    choices = getattr(raw, "choices", None)
    if choices:
        return str(getattr(choices[0], "finish_reason", None) or "unknown")
    return "unknown"


# ===== Router =====


class LLMRouter:
    """Provider Registry 驱动的 Instructor 路由器。

    职责：
      1. 按 adapter.provider 从 PROVIDER_REGISTRY 取 spec，构建 Instructor 客户端
      2. 客户端按 (provider, credentials, params) 三元组缓存
      3. 委托 Instructor 处理结构化输出
      4. 归一化 token usage / finish_reason / 异常
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client_cache: dict[Hashable, instructor.AsyncInstructor] = {}

    def _client_for(self, adapter: ResolvedAdapter) -> instructor.AsyncInstructor:
        spec = PROVIDER_REGISTRY.get(adapter.provider)
        if spec is None:
            raise LLMTransportError(
                f"Unknown provider: {adapter.provider!r}",
                model_id=adapter.model,
            )
        cache_key = (
            adapter.provider,
            frozenset(adapter.credentials.items()),
            frozenset(adapter.params.items()),
        )
        if cache_key not in self._client_cache:
            self._client_cache[cache_key] = spec.build_client(adapter)
        return self._client_cache[cache_key]

    async def complete_structured[T: BaseModel](
        self,
        *,
        adapter: ResolvedAdapter,
        system: str,
        user: str,
        schema: type[T],
        max_retries: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StructuredResponse[T]:
        s = self._settings
        retries = max_retries if max_retries is not None else s.llm_default_max_retries
        temp = temperature if temperature is not None else s.llm_default_temperature
        mt = max_tokens if max_tokens is not None else s.llm_default_max_tokens

        client = self._client_for(adapter)

        try:
            parsed, raw = await client.chat.completions.create_with_completion(
                model=adapter.model,
                response_model=schema,
                max_retries=retries,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=temp,
                max_tokens=mt,
            )
        except InstructorRetryException as e:
            raise StructuredOutputError(
                f"Schema validation failed after {retries} retries: {e}",
                model_id=adapter.model,
                raw_text=str(getattr(e, "last_completion", "") or e),
            ) from e
        except LLMError:
            raise
        except Exception as e:
            raise LLMTransportError(str(e), model_id=adapter.model, cause=e) from e

        return StructuredResponse[T](
            parsed=parsed,
            raw_text=parsed.model_dump_json(),
            model_id=adapter.model,
            usage=_extract_usage(raw),
            finish_reason=_extract_finish_reason(raw),
        )
