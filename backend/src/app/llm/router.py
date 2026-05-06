"""LLM 路由器：Instructor + 直连 anthropic/openai SDK。

公开 API：
    LLMRouter.complete_structured(...) -> StructuredResponse[T]

异常层级：
    LLMError
        ├── LLMTransportError      网络/认证/限流/不识别 model_id
        └── StructuredOutputError  Instructor 重试耗尽 / schema 不匹配

注：default_model_id 与 prefix 路由是过渡实现，Task 4.1 model adapter 体系会替换。
"""

import instructor
from anthropic import AsyncAnthropic
from instructor.core import InstructorRetryException
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict

from app.config import Settings

ANTHROPIC_PREFIXES = ("claude-", "anthropic/")
OPENAI_PREFIXES = ("gpt-", "o1-", "o3-", "openai/")


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
    """网络 / 认证 / 限流 / 不识别 model_id 等传输层异常。"""

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


# ===== 归一化辅助（anthropic Message vs openai ChatCompletion 字段差异）=====


def _extract_usage(raw: object) -> TokenUsage:
    usage = getattr(raw, "usage", None)
    if usage is None:
        return TokenUsage()
    # Anthropic: input_tokens, output_tokens
    if hasattr(usage, "input_tokens"):
        i = int(getattr(usage, "input_tokens", 0) or 0)
        o = int(getattr(usage, "output_tokens", 0) or 0)
        return TokenUsage(input_tokens=i, output_tokens=o, total_tokens=i + o)
    # OpenAI: prompt_tokens, completion_tokens, total_tokens
    if hasattr(usage, "prompt_tokens"):
        return TokenUsage(
            input_tokens=int(getattr(usage, "prompt_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "completion_tokens", 0) or 0),
            total_tokens=int(getattr(usage, "total_tokens", 0) or 0),
        )
    return TokenUsage()


def _extract_finish_reason(raw: object) -> str:
    if hasattr(raw, "stop_reason"):  # Anthropic
        return str(getattr(raw, "stop_reason", None) or "unknown")
    choices = getattr(raw, "choices", None)  # OpenAI
    if choices:
        return str(getattr(choices[0], "finish_reason", None) or "unknown")
    return "unknown"


# ===== Router =====


class LLMRouter:
    """Instructor + 直连 SDK 路由器。

    职责：
      1. 按 model_id 前缀路由到 anthropic 或 openai 的 Instructor 客户端
      2. 委托 Instructor 处理结构化输出（自动 schema 校验 + 失败重试）
      3. 归一化 token usage / finish_reason
      4. 异常归一化（Instructor / SDK 异常 → 三层 LLMError）
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._anthropic: instructor.AsyncInstructor | None = None
        self._openai: instructor.AsyncInstructor | None = None

    def _client_for(self, model_id: str) -> instructor.AsyncInstructor:
        if model_id.startswith(ANTHROPIC_PREFIXES):
            if self._anthropic is None:
                self._anthropic = instructor.from_anthropic(AsyncAnthropic())
            return self._anthropic
        if model_id.startswith(OPENAI_PREFIXES):
            if self._openai is None:
                self._openai = instructor.from_openai(AsyncOpenAI())
            return self._openai
        raise LLMTransportError(
            f"Unsupported model_id prefix: {model_id}",
            model_id=model_id,
        )

    async def complete_structured[T: BaseModel](
        self,
        *,
        model_id: str | None = None,
        system: str,
        user: str,
        schema: type[T],
        max_retries: int | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> StructuredResponse[T]:
        """结构化补全，返回经 pydantic 校验的实例。

        参数为 None 时回退到 Settings 默认值。
        """
        s = self._settings
        mid = model_id or s.default_model_id
        retries = max_retries if max_retries is not None else s.llm_default_max_retries
        temp = temperature if temperature is not None else s.llm_default_temperature
        mt = max_tokens if max_tokens is not None else s.llm_default_max_tokens

        client = self._client_for(mid)

        try:
            parsed, raw = await client.chat.completions.create_with_completion(
                model=mid,
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
                model_id=mid,
                raw_text=str(getattr(e, "last_completion", "") or e),
            ) from e
        except LLMError:
            raise
        except Exception as e:
            raise LLMTransportError(str(e), model_id=mid, cause=e) from e

        return StructuredResponse[T](
            parsed=parsed,
            raw_text=parsed.model_dump_json(),
            model_id=mid,
            usage=_extract_usage(raw),
            finish_reason=_extract_finish_reason(raw),
        )
