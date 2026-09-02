"""真 API 冒烟 — 默认被 `make test` 跳过；`-m smoke` 时真调 embedding 端点。"""
import pytest

from app.config import get_settings
from app.core.embedding import get_embedding_service

pytestmark = pytest.mark.smoke


async def test_real_embedding_dimension_matches_settings() -> None:
    settings = get_settings()
    svc = get_embedding_service()

    vecs = await svc.embed(["M3-22 smoke 测试文本"])

    assert len(vecs) == 1
    assert len(vecs[0]) == settings.embedding_dim
