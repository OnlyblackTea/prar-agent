"""Adapter API 端点测试（mock AdapterService 依赖）。"""

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.adapters import ModelAdapterResponse
from app.main import app

# ===== Mock adapter 数据 =====


def _fake_adapter(
    name: str = "my-claude",
    provider: str = "anthropic",
    model: str = "claude-sonnet-4-6",
    is_default: bool = False,
    is_active: bool = True,
) -> ModelAdapterResponse:
    return ModelAdapterResponse(
        id=uuid4(),
        name=name,
        provider=provider,
        model=model,
        credentials_env={"api_key": "ANTHROPIC_API_KEY"},
        params={},
        is_default=is_default,
        is_active=is_active,
    )


# ===== GET /api/providers =====


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def test_get_providers_returns_registry(client: TestClient) -> None:
    """T6：GET /api/providers 返回 registry 全部条目。"""
    resp = client.get("/api/providers")
    assert resp.status_code == 200
    data = resp.json()
    keys = {p["key"] for p in data["providers"]}
    assert "anthropic" in keys
    assert "openai" in keys
    assert "openai_compatible" in keys


def test_provider_spec_has_fields(client: TestClient) -> None:
    resp = client.get("/api/providers")
    anthropic_spec = next(
        p for p in resp.json()["providers"] if p["key"] == "anthropic"
    )
    assert "api_key" in anthropic_spec["credentials_fields"]
    field = anthropic_spec["credentials_fields"]["api_key"]
    assert field["type"] == "secret_env_name"
    assert field["required"] is True


def test_openai_compatible_has_base_url_param(client: TestClient) -> None:
    resp = client.get("/api/providers")
    compat_spec = next(
        p for p in resp.json()["providers"] if p["key"] == "openai_compatible"
    )
    assert "base_url" in compat_spec["params_fields"]
    assert compat_spec["params_fields"]["base_url"]["type"] == "url"
