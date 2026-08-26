from typing import cast

from pgvector.sqlalchemy import Vector
from sqlalchemy import CheckConstraint, Table, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.db.base import Base
from app.db.models import Comment, Memory, ModelAdapter, Plan, Session


def test_models_register_with_metadata() -> None:
    assert set(Base.metadata.tables.keys()) == {
        "model_adapters",
        "sessions",
        "plans",
        "comments",
        "memories",
    }


# ── ModelAdapter 测试 ──


def test_model_adapter_columns_and_constraints() -> None:
    """T1：ModelAdapter 表存在、列齐全、约束齐全。"""
    table = ModelAdapter.__table__
    assert isinstance(table, Table)
    cols = {c.name for c in table.columns}
    assert cols == {
        "id",
        "name",
        "provider",
        "model",
        "credentials_env",
        "params",
        "is_default",
        "is_active",
        "created_at",
        "updated_at",
    }


def test_model_adapter_name_unique() -> None:
    """T2：UniqueConstraint name 生效。"""
    table = cast(Table, ModelAdapter.__table__)
    unique_constraints = [
        c for c in table.constraints if isinstance(c, UniqueConstraint)
    ]
    assert any(
        {col.name for col in c.columns} == {"name"}
        for c in unique_constraints
    )


def test_model_adapter_default_partial_unique_index() -> None:
    """T3：部分唯一索引 model_adapters_default_unique 存在。"""
    table = cast(Table, ModelAdapter.__table__)
    idx = next(
        (i for i in table.indexes if i.name == "model_adapters_default_unique"),
        None,
    )
    assert idx is not None
    assert idx.unique is True
    assert idx.dialect_options["postgresql"]["where"].text == "is_default = true"


def test_model_adapter_credentials_env_is_jsonb() -> None:
    """T5：credentials_env 和 params 都是 JSONB。"""
    table = ModelAdapter.__table__
    assert isinstance(table.columns["credentials_env"].type, JSONB)
    assert isinstance(table.columns["params"].type, JSONB)


def test_model_adapter_no_provider_check_constraint() -> None:
    """T6：provider 列无 CHECK constraint（合法集由应用层 registry 决定）。"""
    table = cast(Table, ModelAdapter.__table__)
    check_constraints = [
        c for c in table.constraints if isinstance(c, CheckConstraint)
    ]
    provider_checks = [
        c
        for c in check_constraints
        if "provider" in str(c.sqltext)
    ]
    assert provider_checks == []


# ── Session 测试 ──


def test_session_columns_and_check() -> None:
    table = Session.__table__
    assert isinstance(table, Table)
    cols = {c.name for c in table.columns}
    assert cols == {
        "id",
        "init_request",
        "phase",
        "current_plan_version",
        "adapter_id",
        "metadata_json",
        "created_at",
        "updated_at",
    }
    check_constraints = [
        c for c in table.constraints if isinstance(c, CheckConstraint)
    ]
    assert any(c.name == "ck_sessions_phase_valid" for c in check_constraints)


def test_session_adapter_id_fk() -> None:
    """T4：sessions.adapter_id FK 指向 model_adapters.id，ON DELETE RESTRICT。"""
    table = Session.__table__
    adapter_col = table.columns["adapter_id"]
    assert adapter_col.nullable is False
    fks = list(adapter_col.foreign_keys)
    assert len(fks) == 1
    fk = fks[0]
    assert fk.column.table.name == "model_adapters"
    assert fk.column.name == "id"
    assert fk.ondelete == "RESTRICT"


# ── Plan / Comment / Memory 测试（保留原有） ──


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
    assert any(c.name == "ck_memories_kind_valid" for c in check_constraints)


def test_no_relationships_attempted_yet() -> None:
    """守护性测试：本阶段未声明任何 ORM relationship()。"""
    for model in (ModelAdapter, Session, Plan, Comment, Memory):
        rels = list(model.__mapper__.relationships)
        assert rels == [], f"{model.__name__} 不应在此阶段声明 relationship"
