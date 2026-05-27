"""model_adapters table + sessions.model_id → adapter_id

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-28

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. model_adapters 表
    op.create_table(
        "model_adapters",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=False),
        sa.Column(
            "credentials_env",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "params",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "is_default",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            server_default=sa.text("true"),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_model_adapters")),
        sa.UniqueConstraint("name", name="name_unique"),
    )

    # 2. 部分唯一索引：全局至多一行 is_default=true
    op.create_index(
        "model_adapters_default_unique",
        "model_adapters",
        ["is_default"],
        unique=True,
        postgresql_where=sa.text("is_default = true"),
    )

    # 3. sessions: drop model_id, add adapter_id FK
    op.drop_column("sessions", "model_id")
    op.add_column(
        "sessions",
        sa.Column("adapter_id", postgresql.UUID(as_uuid=True), nullable=False),
    )
    op.create_foreign_key(
        op.f("fk_sessions_adapter_id_model_adapters"),
        "sessions",
        "model_adapters",
        ["adapter_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_sessions_adapter_id_model_adapters"), "sessions", type_="foreignkey"
    )
    op.drop_column("sessions", "adapter_id")
    op.add_column(
        "sessions",
        sa.Column("model_id", sa.String(length=128), nullable=True),
    )
    op.drop_index(
        "model_adapters_default_unique", table_name="model_adapters"
    )
    op.drop_table("model_adapters")
