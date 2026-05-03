from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Table, UniqueConstraint

from app.db.base import Base
from app.db.models import Comment, Memory, Plan, Session


def test_models_register_with_metadata() -> None:
    assert set(Base.metadata.tables.keys()) == {
        "sessions",
        "plans",
        "comments",
        "memories",
    }


def test_session_columns_and_check() -> None:
    table = Session.__table__
    assert isinstance(table, Table)
    cols = {c.name for c in table.columns}
    assert cols == {
        "id",
        "init_request",
        "phase",
        "current_plan_version",
        "model_id",
        "metadata_json",
        "created_at",
        "updated_at",
    }
    check_constraints = [
        c for c in table.constraints if isinstance(c, CheckConstraint)
    ]
    # naming_convention 把 name="phase_valid" 渲染成 ck_sessions_phase_valid
    assert any(c.name == "ck_sessions_phase_valid" for c in check_constraints)


def test_plan_unique_constraint() -> None:
    table = Plan.__table__
    assert isinstance(table, Table)
    cols = {c.name for c in table.columns}
    assert cols == {"id", "session_id", "version", "document", "created_at"}
    unique_constraints = [
        c for c in table.constraints if isinstance(c, UniqueConstraint)
    ]
    assert any(
        c.name == "session_version_unique"
        and {col.name for col in c.columns} == {"session_id", "version"}
        for c in unique_constraints
    )


def test_comment_columns() -> None:
    cols = {c.name for c in Comment.__table__.columns}
    assert cols == {
        "id",
        "session_id",
        "plan_version",
        "anchor_id",
        "quote",
        "quote_context",
        "body",
        "resolved",
        "created_at",
    }


def test_memory_vector_and_hnsw() -> None:
    table = Memory.__table__
    assert isinstance(table, Table)

    embedding_col = table.columns["embedding"]
    assert isinstance(embedding_col.type, Vector)
    assert embedding_col.type.dim == 1536
    assert embedding_col.nullable is True

    hnsw = next(
        (i for i in table.indexes if i.name == "memories_embedding_hnsw"),
        None,
    )
    assert hnsw is not None
    assert hnsw.dialect_options["postgresql"]["using"] == "hnsw"
    assert hnsw.dialect_options["postgresql"]["ops"] == {
        "embedding": "vector_cosine_ops"
    }

    check_constraints = [
        c for c in table.constraints if isinstance(c, CheckConstraint)
    ]
    # naming_convention 把 name="kind_valid" 渲染成 ck_memories_kind_valid
    assert any(c.name == "ck_memories_kind_valid" for c in check_constraints)


def test_no_relationships_attempted_yet() -> None:
    """守护性测试：本 task 未声明任何 ORM relationship()，避免后续 task 默认引入。"""
    for model in (Session, Plan, Comment, Memory):
        rels = list(model.__mapper__.relationships)
        assert rels == [], f"{model.__name__} 不应在 Task 02 阶段声明 relationship"
