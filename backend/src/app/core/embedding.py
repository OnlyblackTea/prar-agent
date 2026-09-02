"""Embedding 服务 — OpenAI 兼容端点（默认阿里云百炼 text-embedding-v1，1536 维）。"""
from functools import lru_cache
from typing import Any

from openai import AsyncOpenAI, OpenAIError

from app.config import Settings, get_settings
from app.core.logging import get_logger

_log = get_logger("embedding")


class EmbeddingError(Exception):
    """Embedding 服务错误基类。"""


class EmbeddingTransportError(EmbeddingError):
    """SDK 调用失败（网络/认证/限流等）。"""

    def __init__(
        self,
        message: str,
        *,
        model_id: str,
        cause: BaseException | None = None,
    ) -> None:
        super().__init__(message)
        self.model_id = model_id
        self.cause = cause


class EmbeddingDimensionError(EmbeddingError):
    """返回维度与 Settings.embedding_dim 契约不匹配。"""

    def __init__(
        self, message: str, *, expected: int, actual: int, model_id: str,
    ) -> None:
        super().__init__(message)
        self.expected = expected
        self.actual = actual
        self.model_id = model_id


class EmbeddingService:
    """把文本批量转为向量；客户端懒建并复用连接池。"""

    def __init__(self, settings: Settings, client: AsyncOpenAI | None = None) -> None:
        self._settings = settings
        self._client = client

    def _get_client(self) -> AsyncOpenAI:
        if self._client is None:
            # 值为 None 的项不传，让 SDK 读 OPENAI_API_KEY / OPENAI_BASE_URL 环境变量
            kwargs: dict[str, Any] = {
                "api_key": self._settings.embedding_api_key,
                "base_url": self._settings.embedding_base_url,
            }
            self._client = AsyncOpenAI(**{k: v for k, v in kwargs.items() if v is not None})
        return self._client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            raise ValueError("texts must not be empty")
        if any(not t.strip() for t in texts):
            raise ValueError("texts must not contain blank strings")
        model = self._settings.embedding_model
        try:
            resp = await self._get_client().embeddings.create(model=model, input=texts)
        except OpenAIError as e:
            _log.error("embedding_call_failed", model=model, error=str(e))
            raise EmbeddingTransportError(
                f"embedding call failed for {model!r}: {e}", model_id=model, cause=e,
            ) from e
        vectors = [item.embedding for item in resp.data]
        expected = self._settings.embedding_dim
        for v in vectors:
            if len(v) != expected:
                raise EmbeddingDimensionError(
                    f"embedding dim mismatch: expected {expected}, got {len(v)}. "
                    "Fix: 改 EMBEDDING_DIM 配置 / 换 1536 维模型 / 执行换维迁移（设计文档附录 A）",
                    expected=expected,
                    actual=len(v),
                    model_id=model,
                )
        return vectors

    async def embed_one(self, text: str) -> list[float]:
        vecs = await self.embed([text])
        return vecs[0]


@lru_cache(maxsize=1)
def get_embedding_service() -> EmbeddingService:
    return EmbeddingService(get_settings())
