"""The idempotency keys: `idempotency_keys`, owned by a user or, before sign-in, a device.

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-27
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("key", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=True),
        sa.Column("device_id", sa.Uuid(), nullable=True),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column(
            "response_headers",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("response_body", sa.LargeBinary(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.clock_timestamp(),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(user_id IS NULL) <> (device_id IS NULL)",
            name=op.f("ck_idempotency_keys_one_owner"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_idempotency_keys")),
    )
    op.create_index(
        "uq_idempotency_keys_user_id_key",
        "idempotency_keys",
        ["user_id", "key"],
        unique=True,
        postgresql_where=sa.text("user_id IS NOT NULL"),
    )
    op.create_index(
        "uq_idempotency_keys_device_id_key",
        "idempotency_keys",
        ["device_id", "key"],
        unique=True,
        postgresql_where=sa.text("device_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_idempotency_keys_device_id_key", table_name="idempotency_keys")
    op.drop_index("uq_idempotency_keys_user_id_key", table_name="idempotency_keys")
    op.drop_table("idempotency_keys")
