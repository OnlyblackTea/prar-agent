"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-03

"""
from collections.abc import Sequence

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. 启用 pgvector 扩展（必须在 CREATE TABLE memories 之前）
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    # 2. sessions
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("init_request", sa.Text(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column(
            "current_plan_version",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "phase IN ('init','planning','plan_review','acting','action_review','done')",
            name="ck_sessions_phase_valid",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )

    # 3. plans
    op.create_table(
        "plans",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "document",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_plans_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_plans")),
        sa.UniqueConstraint("session_id", "version", name="session_version_unique"),
    )
    op.create_index(
        op.f("ix_plans_session_id"), "plans", ["session_id"], unique=False
    )

    # 4. comments
    op.create_table(
        "comments",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("plan_version", sa.Integer(), nullable=False),
        sa.Column("anchor_id", sa.String(length=64), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("quote_context", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "resolved",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["sessions.id"],
            name=op.f("fk_comments_session_id_sessions"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_comments")),
    )
    op.create_index(
        op.f("ix_comments_session_id"), "comments", ["session_id"], unique=False
    )

    # 5. memories（含 pgvector embedding 列 + HNSW 索引）
    op.create_table(
        "memories",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", pgvector.sqlalchemy.Vector(1536), nullable=True),
        sa.Column(
            "importance",
            sa.Float(),
            server_default=sa.text("0.5"),
            nullable=False,
        ),
        sa.Column(
            "last_accessed",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "access_count",
            sa.Integer(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("source_session", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "kind IN ('episodic','semantic','procedural')",
            name="ck_memories_kind_valid",
        ),
        sa.ForeignKeyConstraint(
            ["source_session"],
            ["sessions.id"],
            name=op.f("fk_memories_source_session_sessions"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_memories")),
    )
    op.create_index(
        "memories_embedding_hnsw",
        "memories",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )


def downgrade() -> None:
    op.drop_index("memories_embedding_hnsw", table_name="memories")
    op.drop_table("memories")
    op.drop_index(op.f("ix_comments_session_id"), table_name="comments")
    op.drop_table("comments")
    op.drop_index(op.f("ix_plans_session_id"), table_name="plans")
    op.drop_table("plans")
    op.drop_table("sessions")
