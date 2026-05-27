from typing import Any

import instructor
from openai import AsyncOpenAI

from app.llm.providers.base import FieldDef, ProviderSpec, register_provider
from app.llm.types import ResolvedAdapter


def _build_openai_compatible_client(r: ResolvedAdapter) -> instructor.AsyncInstructor:
    return instructor.from_openai(
        AsyncOpenAI(api_key=r.credentials["api_key"], base_url=r.params["base_url"])
    )


def _validate_openai_compatible(payload: dict[str, Any]) -> None:
    params = payload.get("params", {})
    if not params.get("base_url"):
        raise ValueError("base_url required for openai_compatible provider")


@register_provider
def _openai_compatible_spec() -> ProviderSpec:
    return ProviderSpec(
        key="openai_compatible",
        label="OpenAI 兼容接口（DeepSeek / 智谱 / Groq / Ollama 等）",
        credentials_fields={
            "api_key": FieldDef(
                label="API Key 环境变量名",
                type="secret_env_name",
                placeholder="DEEPSEEK_API_KEY",
            ),
        },
        params_fields={
            "base_url": FieldDef(
                label="API Base URL",
                type="url",
                placeholder="https://api.deepseek.com/v1",
            ),
        },
        build_client=_build_openai_compatible_client,
        validate_config=_validate_openai_compatible,
    )
