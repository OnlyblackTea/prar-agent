"""CommentService 单元测试 — 全部 mock AsyncSession。"""
from datetime import UTC, datetime
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


def make_session(**overrides) -> Session:
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


def make_comment_row(**overrides) -> Comment:
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
    async def test_session_not_found(self):
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

    async def test_invalid_plan_version_too_high(self):
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

    async def test_phase_not_review(self):
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

    async def test_quote_not_found_in_plan(self):
        db = AsyncMock()
        session = make_session()
        db.get.return_value = session

        plan = MagicMock(spec=Plan)
        plan.document = {"nodes": [{"type": "paragraph", "text": "nothing relevant"}]}

        async def mock_get_plan(*args, **kwargs):
            return plan

        svc = CommentService(db)
        svc._get_plan = mock_get_plan  # type: ignore[assignment]

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

    async def test_create_success(self):
        db = AsyncMock()
        session = make_session()
        db.get.return_value = session

        plan = MagicMock(spec=Plan)
        plan.document = {
            "nodes": [
                {"type": "paragraph", "text": "hello world from the plan"},
            ],
        }

        async def mock_get_plan(*args, **kwargs):
            return plan

        svc = CommentService(db)
        svc._get_plan = mock_get_plan  # type: ignore[assignment]

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


class TestCommentServiceList:
    async def test_list_by_version(self):
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
    async def test_get_not_found(self):
        db = AsyncMock()
        db.get.return_value = None
        svc = CommentService(db)

        with pytest.raises(CommentNotFoundError):
            await svc.get(uuid4())

    async def test_get_success(self):
        db = AsyncMock()
        expected = make_comment_row()
        db.get.return_value = expected
        svc = CommentService(db)

        result = await svc.get(expected.id)
        assert result is expected


class TestQuoteInPlan:
    def test_quote_found_paragraph(self):
        doc = {"nodes": [{"type": "paragraph", "text": "hello world"}]}
        assert _quote_in_plan("hello", doc) is True

    def test_quote_not_found(self):
        doc = {"nodes": [{"type": "paragraph", "text": "foo bar"}]}
        assert _quote_in_plan("baz", doc) is False

    def test_quote_across_nodes(self):
        doc = {
            "nodes": [
                {"type": "paragraph", "text": "line one"},
                {"type": "heading", "level": 1, "text": "line two"},
            ],
        }
        # joined by \n
        assert _quote_in_plan("one\nline two", doc) is True


class TestExtractText:
    def test_text_field(self):
        assert _extract_text({"text": "hello"}) == "hello"

    def test_decision_fields(self):
        assert _extract_text({"question": "q?", "kind": "single_choice"}) == "q?"

    def test_glossary_fields(self):
        assert _extract_text({"term": "CPU", "definition": "中央处理器"}) == "CPU 中央处理器"
