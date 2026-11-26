"""The outbox: domain events written in the same transaction as the change (EPIC-11 spec, section 2.5).

A module calls `add_event` inside its transaction; the event is stored when that
transaction commits and never when it rolls back. Publishing the stored events is
the worker's job and comes with card F9.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import insert, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.models import OutboxEvent

# One statement takes the next number of a trip. The row lock it takes on the trip's
# counter makes a second transaction of the same trip wait for the first to end.
_NEXT_TRIP_SEQ = text(
    "INSERT INTO outbox_trip_seqs (trip_id, last_seq) VALUES (:trip_id, 1) "
    "ON CONFLICT (trip_id) DO UPDATE SET last_seq = outbox_trip_seqs.last_seq + 1 "
    "RETURNING last_seq"
)


async def add_event(
    session: AsyncSession,
    *,
    trip_id: uuid.UUID,
    type: str,  # the name of tests/INTERFACE.md, F8
    payload: Mapping[str, Any],
) -> int:
    """Add a domain event of a trip to the open transaction of `session`; return its `trip_seq`.

    The first event of a trip gets 1, each next one the previous plus one, with no gap.
    Never commits: the caller's transaction decides whether the event is stored.
    """
    trip_seq: int = (await session.execute(_NEXT_TRIP_SEQ, {"trip_id": trip_id})).scalar_one()
    await session.execute(
        insert(OutboxEvent).values(
            trip_id=trip_id, trip_seq=trip_seq, type=type, payload=dict(payload)
        )
    )
    return trip_seq
