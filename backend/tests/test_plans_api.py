"""GET /plans 与 /plans/{version} API 测试（mock service）。"""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.sessions import get_session_service
from app.db import models
from app.services.session_service import SessionNotFoundError

_NOW = datetime(2026, 8, 25, 12, 0, 0, tzinfo=UTC)


def _session(sid: UUID, current_version: int = 3) -> models.Session:
    return models.Session(
        id=sid,
        init_request="r",
        phase="plan_review",
        current_plan_version=current_version,
        adapter_id=uuid4(),
    )


def _plan(sid: UUID, version: int, node_count: int = 2) -> models.Plan:
    return models.Plan(
        id=uuid4(),
        session_id=sid,
        version=version,
        document={
            "title": "T",
            "summary": "S",
            "nodes": [{"type": "paragraph", "text": "x"}] * node_count,
        },
        created_at=_NOW,
    )


def test_list_plans_ok(client: TestClient) -> None:
    """case 1: 200 + current_version + versions 升序 + node_count 现算。"""
    sid = uuid4()
    service = AsyncMock()
    service.list_plans.return_value = (
        _session(sid, 3), [_plan(sid, 1), _plan(sid, 2, node_count=3), _plan(sid, 3)],
    )

    cast(FastAPI, client.app).dependency_overrides[get_session_service] = lambda: service
    try:
        resp = client.get(f"/api/sessions/{sid}/plans")
    finally:
        cast(FastAPI, client.app).dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["session_id"] == str(sid)
    assert body["current_version"] == 3
    assert [v["version"] for v in body["versions"]] == [1, 2, 3]
    assert [v["node_count"] for v in body["versions"]] == [2, 3, 2]


def test_list_plans_session_not_found(client: TestClient) -> None:
    """case 2: session 不存在 → 404。"""
    service = AsyncMock()
    service.list_plans.side_effect = SessionNotFoundError(uuid4())

    cast(FastAPI, client.app).dependency_overrides[get_session_service] = lambda: service
    try:
        resp = client.get(f"/api/sessions/{uuid4()}/plans")
    finally:
        cast(FastAPI, client.app).dependency_overrides.clear()

    assert resp.status_code == 404
    assert resp.json()["detail"] == "session_not_found"


def test_get_plan_version_ok(client: TestClient) -> None:
    """case 3: 200 + document 完整。"""
    sid = uuid4()
    service = AsyncMock()
    service.get_plan.return_value = _plan(sid, 2)

    cast(FastAPI, client.app).dependency_overrides[get_session_service] = lambda: service
    try:
        resp = client.get(f"/api/sessions/{sid}/plans/2")
    finally:
        cast(FastAPI, client.app).dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 2
    assert body["document"]["title"] == "T"


def test_get_plan_version_not_found(client: TestClient) -> None:
    """case 4: 越界版本 → 404 plan_version_not_found。"""
    sid = uuid4()
    service = AsyncMock()
    service.get_plan.side_effect = ValueError("plan_version_not_found")

    cast(FastAPI, client.app).dependency_overrides[get_session_service] = lambda: service
    try:
        resp = client.get(f"/api/sessions/{sid}/plans/99")
    finally:
        cast(FastAPI, client.app).dependency_overrides.clear()

    assert resp.status_code == 404
    assert resp.json()["detail"] == "plan_version_not_found"


def test_get_plan_version_session_not_found(client: TestClient) -> None:
    """case 5: session 不存在 → 404。"""
    service = AsyncMock()
    service.get_plan.side_effect = SessionNotFoundError(uuid4())

    cast(FastAPI, client.app).dependency_overrides[get_session_service] = lambda: service
    try:
        resp = client.get(f"/api/sessions/{uuid4()}/plans/1")
    finally:
        cast(FastAPI, client.app).dependency_overrides.clear()

    assert resp.status_code == 404
    assert resp.json()["detail"] == "session_not_found"
