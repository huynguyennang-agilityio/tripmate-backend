"""The platform's tables (EPIC-11 spec, section 4.2; data-model.md, section 6.2).

`platform` is not a module, but it owns tables: migrations load this file through
`import_models()`. Today these are the outbox's two tables.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, Identity, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


class OutboxEvent(Base):
    """A domain event, stored in the same transaction as the change that caused it.

    `trip_seq` numbers a trip's events without gaps (see `OutboxTripSeq`). There is no
    foreign key on `trip_id` yet: EPIC-02 adds the `trips` table and the key.
    """

    __tablename__ = "outbox_events"
    __table_args__ = (UniqueConstraint("trip_id", "trip_seq"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    trip_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    trip_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OutboxTripSeq(Base):
    """The last `trip_seq` handed out for a trip: one row per trip.

    `add_event` raises `last_seq` by one in the caller's own transaction. The row lock
    of that update makes concurrent transactions of one trip take turns, and a rollback
    undoes the increment, so the numbers have no gap. The counter is a table of its own
    and not `max(trip_seq) + 1` over `outbox_events`, because the worker deletes
    published events after 7 days: the maximum would fall back and the numbering would
    restart at 1, and a client could no longer tell whether it missed an event.
    """

    __tablename__ = "outbox_trip_seqs"

    trip_id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    last_seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
