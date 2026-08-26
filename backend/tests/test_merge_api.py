"""POST /api/sessions/{id}/merge API 测试（mock service + router 依赖）。"""

from typing import cast
from unittest.mock import AsyncMock
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.sessions import get_router_dep, get_session_service
from app.core.merger_schemas import MergerAction, MergerResult
from app.core.plan_schemas import ParagraphNode, PlanDocument
from app.services.session_service import SessionNotFoundError


def _plan() -> PlanDocument:
    return PlanDocument(
        title="T",
        summary="S",
        nodes=[ParagraphNode(text="revised text")],
    )


def test_merge_ok(client: TestClient) -> None:
    """case 1: 正常 merge → 200 + plan_version=2 + plan_changed=true。"""
    comment_id = uuid4()
    merger_result = MergerResult(
        actions=[
            MergerAction(comment_id=comment_id, decision="accept", reason="ok"),
        ],
        overall_comment="done",
    )
    service = AsyncMock()
    service.merge_plan.return_value = (_plan(), merger_result, 2)

    cast(FastAPI, client.app).dependency_overrides[get_session_service] = lambda: service
    cast(FastAPI, client.app).dependency_overrides[get_router_dep] = lambda: AsyncMock()
    try:
        resp = client.post(f"/api/sessions/{uuid4()}/merge")
    finally:
        cast(FastAPI, client.app).dependency_overrides.clear()

    assert resp.status_code == 200
    body = resp.json()
    assert body["plan_version"] == 2
    assert body["plan"]["nodes"][0]["text"] == "revised text"
    assert body["merger_result"]["actions"][0]["decision"] == "accept"
    assert body["plan_changed"] is True


def test_merge_phase_not_review(client: TestClient) -> None:
    """case 2: phase 非 plan_review → 409。"""
    service = AsyncMock()
    service.merge_plan.side_effect = ValueError("phase_not_review")

    cast(FastAPI, client.app).dependency_overrides[get_session_service] = lambda: service
    cast(FastAPI, client.app).dependency_overrides[get_router_dep] = lambda: AsyncMock()
    try:
        resp = client.post(f"/api/sessions/{uuid4()}/merge")
    finally:
        cast(FastAPI, client.app).dependency_overrides.clear()

    assert resp.status_code == 409
    assert resp.json()["detail"] == "phase_not_review"


def test_merge_no_comments(client: TestClient) -> None:
    """case 3: 无 unresolved comments → 400。"""
    service = AsyncMock()
    service.merge_plan.side_effect = ValueError("no_comments_to_merge")

    cast(FastAPI, client.app).dependency_overrides[get_session_service] = lambda: service
    cast(FastAPI, client.app).dependency_overrides[get_router_dep] = lambda: AsyncMock()
    try:
        resp = client.post(f"/api/sessions/{uuid4()}/merge")
    finally:
        cast(FastAPI, client.app).dependency_overrides.clear()

    assert resp.status_code == 400
    assert resp.json()["detail"] == "no_comments_to_merge"


def test_merge_session_not_found(client: TestClient) -> None:
    """补充：session 不存在 → 404。"""
    service = AsyncMock()
    service.merge_plan.side_effect = SessionNotFoundError(uuid4())

    cast(FastAPI, client.app).dependency_overrides[get_session_service] = lambda: service
    cast(FastAPI, client.app).dependency_overrides[get_router_dep] = lambda: AsyncMock()
    try:
        resp = client.post(f"/api/sessions/{uuid4()}/merge")
    finally:
        cast(FastAPI, client.app).dependency_overrides.clear()

    assert resp.status_code == 404
    assert resp.json()["detail"] == "session_not_found"
