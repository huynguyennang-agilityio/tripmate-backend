"""Throwaway F14 checkpoint 3: an empty migration (do not merge)."""

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass

# Throwaway: a later push after the approval.
