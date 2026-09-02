from functools import lru_cache

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

# 让 .env 的变量进 os.environ（override=False 不覆盖真环境变量）：
# AdapterService.resolve 与 SDK 隐式读取都依赖进程环境变量，
# pydantic settings 只载入 Settings 实例不导出。
load_dotenv()


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

    # Embedding 服务（M4 长期记忆底座；OpenAI 兼容端点）
    embedding_model: str = "text-embedding-v1"
    embedding_dim: int = 1536  # 运行时契约，与 memories.embedding Vector(1536) 对齐
    embedding_base_url: str | None = None  # None → SDK 读 OPENAI_BASE_URL
    embedding_api_key: str | None = None  # None → SDK 读 OPENAI_API_KEY

    # M4 Consolidator 后台任务
    consolidator_interval_seconds: int = 3600


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
