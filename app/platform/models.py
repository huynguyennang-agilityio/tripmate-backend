"""The platform's tables (EPIC-11 spec, section 4.2; data-model.md, section 6.2).

`platform` is not a module, but it owns tables: migrations load this file through
`import_models()`. Today these are the outbox's two tables and `idempotency_keys`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
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


class IdempotencyKey(Base):
    """An `Idempotency-Key` with its owner, its request, and the response stored for it.

    The owner is `user_id` when the caller is signed in and `device_id` before sign-in
    (data-model.md, section 6.3), so exactly one of them is set, and the key is unique per
    owner. A row with no `status` and no `completed_at` is a request still running. Cleanup
    of old rows is card F9's.
    """

    __tablename__ = "idempotency_keys"
    __table_args__ = (
        CheckConstraint("(user_id IS NULL) <> (device_id IS NULL)", name="one_owner"),
        Index(
            "uq_idempotency_keys_user_id_key",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
        Index(
            "uq_idempotency_keys_device_id_key",
            "device_id",
            "key",
            unique=True,
            postgresql_where=text("device_id IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    key: Mapped[uuid.UUID] = mapped_column(nullable=False)
    user_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    device_id: Mapped[uuid.UUID | None] = mapped_column(nullable=True)
    method: Mapped[str] = mapped_column(Text, nullable=False)
    path: Mapped[str] = mapped_column(Text, nullable=False)
    request_hash: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_headers: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    response_body: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.clock_timestamp()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
