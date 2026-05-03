from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def test_health_returns_200_and_status_ok(client: TestClient) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_health_response_schema_complete(client: TestClient) -> None:
    resp = client.get("/health")
    assert set(resp.json().keys()) == {"status", "service", "version", "environment"}


@pytest.fixture
def reset_settings_cache() -> Iterator[None]:
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def test_health_reflects_settings(
    monkeypatch: pytest.MonkeyPatch,
    reset_settings_cache: None,
) -> None:
    monkeypatch.setenv("APP_NAME", "test-app")
    fresh_client = TestClient(create_app())
    resp = fresh_client.get("/health")
    assert resp.json()["service"] == "test-app"
