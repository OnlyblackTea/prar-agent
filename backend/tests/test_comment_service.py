"""CommentService 单元测试 — 全部 mock AsyncSession。"""
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.core.comment_schemas import CommentCreate
from app.db.models import Comment, Plan, Session
from app.services.comment_service import (
    CommentNotFoundError,
    CommentService,
    _extract_text,
    _quote_in_plan,
)
from app.services.session_service import SessionNotFoundError


def make_session(**overrides: Any) -> Session:
    defaults = {
        "id": uuid4(),
        "init_request": "test",
        "phase": "plan_review",
        "current_plan_version": 1,
        "adapter_id": uuid4(),
        "metadata_json": {},
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    s = MagicMock(spec=Session)
    for k, v in defaults.items():
        setattr(s, k, v)
    return s


def make_comment_row(**overrides: Any) -> Comment:
    defaults = {
        "id": uuid4(),
        "session_id": uuid4(),
        "plan_version": 1,
        "anchor_id": "abc123",
        "quote": "hello world",
        "quote_context": "context...",
        "body": "my comment",
        "resolved": False,
        "created_at": datetime.now(UTC),
    }
    defaults.update(overrides)
    c = MagicMock(spec=Comment)
    for k, v in defaults.items():
        setattr(c, k, v)
    return c


class TestCommentServiceCreate:
    async def test_session_not_found(self) -> None:
        db = AsyncMock()
        db.get.return_value = None
        svc = CommentService(db)

        with pytest.raises(SessionNotFoundError):
            await svc.create(
                session_id=uuid4(),
                payload=CommentCreate(
                    anchor_id="a1",
                    plan_version=1,
                    quote="hello",
                    quote_context="ctx",
                    body="test",
                ),
            )

    async def test_invalid_plan_version_too_high(self) -> None:
        db = AsyncMock()
        session = make_session(current_plan_version=1)
        db.get.return_value = session
        svc = CommentService(db)

        with pytest.raises(ValueError, match="invalid_plan_version"):
            await svc.create(
                session_id=session.id,
                payload=CommentCreate(
                    anchor_id="a1",
                    plan_version=99,
                    quote="hello",
                    quote_context="ctx",
                    body="test",
                ),
            )

    async def test_phase_not_review(self) -> None:
        db = AsyncMock()
        session = make_session(phase="acting")
        db.get.return_value = session
        svc = CommentService(db)

        with pytest.raises(ValueError, match="phase_not_review"):
            await svc.create(
                session_id=session.id,
                payload=CommentCreate(
                    anchor_id="a1",
                    plan_version=1,
                    quote="hello",
                    quote_context="ctx",
                    body="test",
                ),
            )

    async def test_quote_not_found_in_plan(self) -> None:
        db = AsyncMock()
        session = make_session()
        db.get.return_value = session

        plan = MagicMock(spec=Plan)
        plan.document = {"nodes": [{"type": "paragraph", "text": "nothing relevant"}]}

        async def mock_get_plan(*args: Any, **kwargs: Any) -> MagicMock:
            return plan

        svc = CommentService(db)
        svc._get_plan = mock_get_plan  # type: ignore[method-assign]

        with pytest.raises(ValueError, match="quote_not_found_in_plan"):
            await svc.create(
                session_id=session.id,
                payload=CommentCreate(
                    anchor_id="a1",
                    plan_version=1,
                    quote="not in the doc",
                    quote_context="ctx",
                    body="test",
                ),
            )

    async def test_create_success(self) -> None:
        db = AsyncMock()
        session = make_session()
        db.get.return_value = session

        plan = MagicMock(spec=Plan)
        plan.document = {
            "nodes": [
                {"type": "paragraph", "text": "hello world from the plan"},
            ],
        }

        async def mock_get_plan(*args: Any, **kwargs: Any) -> MagicMock:
            return plan

        svc = CommentService(db)
        svc._get_plan = mock_get_plan  # type: ignore[method-assign]

        payload = CommentCreate(
            anchor_id="a1",
            plan_version=1,
            quote="hello world",
            quote_context="ctx",
            body="a comment",
        )
        result = await svc.create(session_id=session.id, payload=payload)

        db.add.assert_called_once()
        db.flush.assert_called()
        db.refresh.assert_called_once()
        assert result is not None

    async def test_action_review_allows_step_comment(self) -> None:
        """M4-27 D2：action_review 下对 step 结果评论，quote = step title。"""
        db = AsyncMock()
        session = make_session(phase="action_review")
        db.get.return_value = session

        plan = MagicMock(spec=Plan)
        plan.document = {
            "nodes": [
                {
                    "type": "step",
                    "id": "step_000",
                    "title": "构建镜像",
                    "description": "docker build",
                    "tool": "shell",
                    "tool_args": {},
                    "rerunnable": True,
                },
            ],
        }

        async def mock_get_plan(*args: Any, **kwargs: Any) -> MagicMock:
            return plan

        svc = CommentService(db)
        svc._get_plan = mock_get_plan  # type: ignore[method-assign]

        result = await svc.create(
            session_id=session.id,
            payload=CommentCreate(
                anchor_id="step:step_000",
                plan_version=1,
                quote="构建镜像",
                quote_context="exit 1",
                body="先修 Dockerfile 再重跑",
            ),
        )

        db.add.assert_called_once()
        assert result is not None


class TestCommentServiceList:
    async def test_list_by_version(self) -> None:
        db = AsyncMock()
        c1 = make_comment_row(created_at=datetime(2025, 1, 1, tzinfo=UTC))
        c2 = make_comment_row(created_at=datetime(2025, 1, 2, tzinfo=UTC))

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [c1, c2]
        db.execute.return_value = mock_result

        svc = CommentService(db)
        session_id = uuid4()
        result = await svc.list_by_version(session_id=session_id, plan_version=1)

        assert len(result) == 2
        assert result[0] is c1
        assert result[1] is c2


class TestCommentServiceGet:
    async def test_get_not_found(self) -> None:
        db = AsyncMock()
        db.get.return_value = None
        svc = CommentService(db)

        with pytest.raises(CommentNotFoundError):
            await svc.get(uuid4())

    async def test_get_success(self) -> None:
        db = AsyncMock()
        expected = make_comment_row()
        db.get.return_value = expected
        svc = CommentService(db)

        result = await svc.get(expected.id)
        assert result is expected


class TestQuoteInPlan:
    def test_quote_found_paragraph(self) -> None:
        doc = {"nodes": [{"type": "paragraph", "text": "hello world"}]}
        assert _quote_in_plan("hello", doc) is True

    def test_quote_not_found(self) -> None:
        doc = {"nodes": [{"type": "paragraph", "text": "foo bar"}]}
        assert _quote_in_plan("baz", doc) is False

    def test_quote_across_nodes(self) -> None:
        doc = {
            "nodes": [
                {"type": "paragraph", "text": "line one"},
                {"type": "heading", "level": 1, "text": "line two"},
            ],
        }
        # joined by \n
        assert _quote_in_plan("one\nline two", doc) is True

    def test_quote_found_step_title(self) -> None:
        """M4-27 D2：step title 是 plan 可见内容，可作为 quote。"""
        doc = {
            "nodes": [
                {
                    "type": "step",
                    "id": "step_000",
                    "title": "构建镜像",
                    "description": "docker build -t app .",
                    "tool": "shell",
                    "tool_args": {},
                    "rerunnable": True,
                },
            ],
        }
        assert _quote_in_plan("构建镜像", doc) is True


class TestExtractText:
    def test_text_field(self) -> None:
        assert _extract_text({"text": "hello"}) == "hello"

    def test_decision_fields(self) -> None:
        assert _extract_text({"question": "q?", "kind": "single_choice"}) == "q?"

    def test_glossary_fields(self) -> None:
        assert _extract_text({"term": "CPU", "definition": "中央处理器"}) == "CPU 中央处理器"

    def test_step_fields(self) -> None:
        """M4-27 D2：title 参与抽取，排在 description 之前。"""
        assert (
            _extract_text({"title": "构建镜像", "description": "docker build"})
            == "构建镜像 docker build"
        )
