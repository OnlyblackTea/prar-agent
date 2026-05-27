from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "prar-agent-backend"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"
    log_format: str = "console"

    host: str = "0.0.0.0"
    port: int = 8000

    database_url: str = "postgresql+asyncpg://prar:prar@localhost:15432/prar_agent"

    # LLM 默认参数（默认 adapter 由 DB is_default 列管理）
    llm_default_temperature: float = 0.7
    llm_default_max_tokens: int = 4096
    llm_default_max_retries: int = 2


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
