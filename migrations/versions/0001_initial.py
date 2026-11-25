"""The first migration: empty.

The EPIC-11 tables `idempotency_keys` and `outbox_events` come with cards F7 and
F8; each module's tables come with the module.

Revision ID: 0001
Revises:
Create Date: 2026-11-25
"""

from __future__ import annotations

from collections.abc import Sequence

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
