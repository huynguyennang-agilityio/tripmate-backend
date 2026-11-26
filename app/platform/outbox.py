"""The outbox: domain events written in the same transaction as the change (EPIC-11 spec, section 2.5).

A module calls `add_event` inside its transaction; the event is stored when that
transaction commits and never when it rolls back. Publishing the stored events is
the worker's job and comes with card F9.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# One statement takes the trip's next number and stores the event with it, so the number
# is never taken without its row. The row lock on the trip's counter makes a second
# transaction of the same trip wait for the first to end.
_ADD_EVENT = text(
    "WITH next AS ("
    "INSERT INTO outbox_trip_seqs (trip_id, last_seq) VALUES (:trip_id, 1) "
    "ON CONFLICT (trip_id) DO UPDATE SET last_seq = outbox_trip_seqs.last_seq + 1 "
    "RETURNING last_seq"
    ") "
    "INSERT INTO outbox_events (trip_id, trip_seq, type, payload) "
    "SELECT :trip_id, last_seq, :type, CAST(:payload AS jsonb) FROM next "
    "RETURNING trip_seq"
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

    A payload that is not JSON raises before any SQL is sent, so no number is taken.

    The trip's counter row is locked until the caller's transaction ends: another
    transaction that adds an event of the same trip waits for it. So call this late in
    the transaction, after the other work, and add the events of one trip in one
    transaction, never across several.
    """
    payload_json = json.dumps(payload)
    result = await session.execute(
        _ADD_EVENT,
        {"trip_id": trip_id, "type": type, "payload": payload_json},
    )
    return int(result.scalar_one())
