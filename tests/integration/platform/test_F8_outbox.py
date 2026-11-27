"""F8 Outbox: cases F8-1 to F8-5 (epic-11 spec, sections 2.5 and 4.2; data-model.md, 6.2).

A module adds a domain event with one call, `add_event`, inside its own transaction; the
event's `trip_seq` is assigned in that transaction, per trip, with no gap. The tests bring
the database of TEST_DATABASE_URL to the head migration, add events through the request's
session or through sessions of their own, and read `outbox_events` back through a connection
of their own after the commit (tests/INTERFACE.md, section "F8: Outbox").
"""

from __future__ import annotations

import asyncio
import json
import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support import (
    APP_DIR,
    build_app,
    client_for,
    database_engine,
    real_database_url,
    run_alembic,
)

# A step that waits on the database longer than this has hung (for example a deadlock).
STEP_TIMEOUT = 15.0

EVENT_TYPE = "expense.changed"


def _output(result: Any) -> str:
    return (
        f"exit code {result.returncode}\n--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}"
    )


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> Iterator[None]:
    """The database of TEST_DATABASE_URL at the head migration (tests/INTERFACE.md, F3)."""
    result = run_alembic(real_database_url(), "upgrade", "head")
    assert result.returncode == 0, _output(result)
    yield


@pytest.fixture
async def engine() -> AsyncIterator[Any]:
    async with database_engine(real_database_url()) as engine:
        yield engine


async def _add_event(session: AsyncSession, trip_id: uuid.UUID, payload: dict[str, Any]) -> int:
    # Imported here, so each test fails on its own while the code does not exist yet.
    from app.platform.outbox import add_event

    return await asyncio.wait_for(
        add_event(session, trip_id=trip_id, type=EVENT_TYPE, payload=payload),
        timeout=STEP_TIMEOUT,
    )


async def _add_and_commit(engine: Any, trip_id: uuid.UUID, payload: dict[str, Any]) -> int:
    """One transaction of a session of the test's own that adds one event and commits."""
    async with AsyncSession(engine, expire_on_commit=False) as session, session.begin():
        return await _add_event(session, trip_id, payload)


async def _outbox_rows(trip_id: uuid.UUID | None = None) -> list[dict[str, Any]]:
    """The rows of `outbox_events` (of one trip, or all), in `id` order."""
    query = (
        "SELECT id, trip_id, trip_seq, type, payload::text AS payload, created_at, "
        "published_at FROM outbox_events"
    )
    params: dict[str, Any] = {}
    if trip_id is not None:
        query += " WHERE trip_id = :trip_id"
        params["trip_id"] = trip_id
    query += " ORDER BY id"
    async with database_engine(real_database_url()) as engine, engine.connect() as connection:
        rows = (await connection.execute(text(query), params)).mappings().all()
    return [{**row, "payload": json.loads(row["payload"])} for row in rows]


async def _trip_seqs(trip_id: uuid.UUID) -> list[int]:
    return [row["trip_seq"] for row in await _outbox_rows(trip_id)]


# --- F8-1 ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F8_1_transaction_that_adds_one_event_and_commits_stores_one_unpublished_row(
    engine,
) -> None:
    trip_id = uuid.uuid4()
    payload = {"id": str(uuid.uuid4()), "version": 3}

    trip_seq = await _add_and_commit(engine, trip_id, payload)

    rows = await _outbox_rows()
    assert len(rows) == 1, rows
    row = rows[0]
    # The first event of a new trip is the trip's first: trip_seq 1.
    assert trip_seq == 1
    assert row["trip_id"] == trip_id
    assert row["trip_seq"] == 1
    assert row["type"] == EVENT_TYPE
    assert row["payload"] == payload
    assert row["created_at"] is not None
    assert row["published_at"] is None


@pytest.mark.asyncio
async def test_F8_1_event_added_after_an_earlier_committed_event_gets_the_trips_next_trip_seq(
    engine,
) -> None:
    trip_id = uuid.uuid4()
    first = await _add_and_commit(engine, trip_id, {"id": "first", "version": 1})

    second = await _add_and_commit(engine, trip_id, {"id": "second", "version": 2})

    assert (first, second) == (1, 2)
    rows = await _outbox_rows(trip_id)
    assert [(row["trip_seq"], row["payload"]["id"]) for row in rows] == [
        (1, "first"),
        (2, "second"),
    ]
    assert [row["published_at"] for row in rows] == [None, None]


# --- F8-2 ---------------------------------------------------------------------------------

# tests/INTERFACE.md, sections "F3: Database session" and "F8: Outbox".
API_SOURCE = """\
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db import get_session
from app.platform.outbox import add_event

router = APIRouter()

INSERT = text("INSERT INTO {table} (marker) VALUES (:marker)")


@router.post("/{segment}/returns")
async def write_add_event_and_return(
    marker: str, trip_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, int]:
    await session.execute(INSERT, {{"marker": marker}})
    trip_seq = await add_event(
        session, trip_id=trip_id, type="expense.changed", payload={{"marker": marker}}
    )
    return {{"trip_seq": trip_seq}}


@router.post("/{segment}/raises")
async def write_add_event_and_raise(
    marker: str, trip_id: uuid.UUID, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, int]:
    await session.execute(INSERT, {{"marker": marker}})
    await add_event(
        session, trip_id=trip_id, type="expense.changed", payload={{"marker": marker}}
    )
    raise RuntimeError("F8-2 probe: the handler raises after writing and adding an event")
"""


@pytest.fixture
async def probe_table() -> AsyncIterator[str]:
    """A table of the test's own on the real PostgreSQL, dropped after the test."""
    name = f"f8probe_{uuid.uuid4().hex[:8]}"
    async with database_engine(real_database_url()) as engine:
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE TABLE {name} (marker text NOT NULL)"))
        try:
            yield name
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP TABLE IF EXISTS {name}"))


async def _stored_markers(table: str) -> list[str]:
    async with database_engine(real_database_url()) as engine, engine.connect() as connection:
        # The table name is made by the test, never taken from input.
        rows = await connection.execute(text(f"SELECT marker FROM {table}"))  # noqa: S608
        return [row[0] for row in rows]


def _probe_module(module_folder, table: str) -> str:
    name = module_folder({"__init__.py": ""})
    segment = name.replace("_", "-")
    source = API_SOURCE.format(table=table, segment=segment)
    (APP_DIR / name / "api.py").write_text(source, encoding="utf-8")
    return f"/v1/{segment}"


def _headers() -> dict[str, str]:
    # X-Device-Id is asked for on every request but the health check (spec, section 2.2).
    return {"X-Device-Id": str(uuid.uuid4())}


@pytest.mark.asyncio
async def test_F8_2_request_that_writes_a_record_adds_an_event_then_fails_stores_neither(
    set_settings, module_folder, probe_table
) -> None:
    set_settings(DATABASE_URL=real_database_url(), RATE_LIMIT_PER_MINUTE="0")
    path = _probe_module(module_folder, probe_table)
    marker = f"f8-2-{uuid.uuid4().hex[:8]}"
    control_trip = uuid.uuid4()
    failed_trip = uuid.uuid4()

    async with client_for(build_app(), raise_app_exceptions=False) as client:
        # The same route shape stores the record and the event when the handler returns.
        control = await client.post(
            f"{path}/returns",
            params={"marker": f"{marker}-control", "trip_id": str(control_trip)},
            headers=_headers(),
        )
        assert control.status_code == 200, f"{control.status_code} {control.text}"
        await client.post(
            f"{path}/raises",
            params={"marker": marker, "trip_id": str(failed_trip)},
            headers=_headers(),
        )

    assert await _stored_markers(probe_table) == [f"{marker}-control"]
    assert await _outbox_rows(failed_trip) == []
    rows = await _outbox_rows()
    assert [(row["trip_id"], row["payload"]) for row in rows] == [
        (control_trip, {"marker": f"{marker}-control"})
    ]


class _ProbeError(Exception):
    """Raised by the test inside its own transaction, after adding an event."""


@pytest.mark.asyncio
async def test_F8_2_transaction_of_a_session_that_adds_an_event_then_fails_stores_neither(
    engine, probe_table
) -> None:
    trip_id = uuid.uuid4()
    marker = f"f8-2-session-{uuid.uuid4().hex[:8]}"

    with pytest.raises(_ProbeError):
        async with AsyncSession(engine) as session, session.begin():
            await session.execute(
                text(f"INSERT INTO {probe_table} (marker) VALUES (:marker)"),  # noqa: S608
                {"marker": marker},
            )
            await _add_event(session, trip_id, {"marker": marker})
            raise _ProbeError

    assert await _stored_markers(probe_table) == []
    assert await _outbox_rows() == []

    # The number of the event that was rolled back is used again: no gap.
    assert await _add_and_commit(engine, trip_id, {"marker": f"{marker}-after"}) == 1
    assert await _trip_seqs(trip_id) == [1]


# --- F8-3 ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F8_3_transaction_that_adds_two_events_stores_consecutive_trip_seq_in_order(
    engine,
) -> None:
    trip_id = uuid.uuid4()

    async with AsyncSession(engine) as session, session.begin():
        first = await _add_event(session, trip_id, {"id": "first", "version": 1})
        second = await _add_event(session, trip_id, {"id": "second", "version": 2})

    assert (first, second) == (1, 2)
    rows = await _outbox_rows(trip_id)
    # In id order, the events come in the order they were added, numbered 1 then 2.
    assert [(row["trip_seq"], row["payload"]["id"]) for row in rows] == [
        (1, "first"),
        (2, "second"),
    ]


# --- F8-4 ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F8_4_two_transactions_of_the_same_trip_at_the_same_time_get_distinct_gapless_seq(
    engine,
) -> None:
    trip_id = uuid.uuid4()

    async with AsyncSession(engine) as first_session:
        await first_session.begin()
        first = await _add_event(first_session, trip_id, {"id": "first"})

        # The second transaction adds its event while the first is still open, then both
        # commit.
        second_task = asyncio.create_task(_add_and_commit(engine, trip_id, {"id": "second"}))
        try:
            await asyncio.wait({second_task}, timeout=0.5)
            await asyncio.wait_for(first_session.commit(), timeout=STEP_TIMEOUT)
            second = await asyncio.wait_for(second_task, timeout=STEP_TIMEOUT)
        finally:
            if not second_task.done():
                second_task.cancel()

    assert first != second, (first, second)
    assert sorted([first, second]) == [1, 2]
    assert await _trip_seqs(trip_id) == [1, 2]


# --- F8-5 ---------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F8_5_events_of_two_trips_are_numbered_per_trip(engine) -> None:
    trip_a = uuid.uuid4()
    trip_b = uuid.uuid4()

    async with AsyncSession(engine) as session, session.begin():
        a1 = await _add_event(session, trip_a, {"id": "a1"})
        b1 = await _add_event(session, trip_b, {"id": "b1"})
    async with AsyncSession(engine) as session, session.begin():
        b2 = await _add_event(session, trip_b, {"id": "b2"})
        a2 = await _add_event(session, trip_a, {"id": "a2"})
    b3 = await _add_and_commit(engine, trip_b, {"id": "b3"})

    assert (a1, a2) == (1, 2)
    assert (b1, b2, b3) == (1, 2, 3)
    rows_a = await _outbox_rows(trip_a)
    rows_b = await _outbox_rows(trip_b)
    assert [(row["trip_seq"], row["payload"]["id"]) for row in rows_a] == [(1, "a1"), (2, "a2")]
    assert [(row["trip_seq"], row["payload"]["id"]) for row in rows_b] == [
        (1, "b1"),
        (2, "b2"),
        (3, "b3"),
    ]
