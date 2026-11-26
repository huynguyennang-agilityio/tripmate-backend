"""The outbox: `outbox_events` and the per-trip counter `outbox_trip_seqs`.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.BigInteger(), sa.Identity(), nullable=False),
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("trip_seq", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_outbox_events")),
        sa.UniqueConstraint("trip_id", "trip_seq", name=op.f("uq_outbox_events_trip_id_trip_seq")),
    )
    op.create_table(
        "outbox_trip_seqs",
        sa.Column("trip_id", sa.Uuid(), nullable=False),
        sa.Column("last_seq", sa.BigInteger(), nullable=False),
        sa.PrimaryKeyConstraint("trip_id", name=op.f("pk_outbox_trip_seqs")),
    )


def downgrade() -> None:
    op.drop_table("outbox_trip_seqs")
    op.drop_table("outbox_events")
