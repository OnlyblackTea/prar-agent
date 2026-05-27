import instructor
from openai import AsyncOpenAI

from app.llm.providers.base import FieldDef, ProviderSpec, register_provider
from app.llm.types import ResolvedAdapter


def _build_openai_client(r: ResolvedAdapter) -> instructor.AsyncInstructor:
    return instructor.from_openai(
        AsyncOpenAI(api_key=r.credentials["api_key"])
    )


@register_provider
def _openai_spec() -> ProviderSpec:
    return ProviderSpec(
        key="openai",
        label="OpenAI",
        credentials_fields={
            "api_key": FieldDef(
                label="API Key 环境变量名",
                type="secret_env_name",
                placeholder="OPENAI_API_KEY",
                validate_pattern=r"^[A-Z][A-Z0-9_]*$",
            ),
        },
        build_client=_build_openai_client,
    )
