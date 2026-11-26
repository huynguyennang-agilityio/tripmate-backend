"""F8: a payload that is not JSON stores nothing and takes no `trip_seq` (review round 1, R1-1)."""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support import database_engine, real_database_url, run_alembic


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> Iterator[None]:
    result = run_alembic(real_database_url(), "upgrade", "head")
    assert result.returncode == 0, f"{result.stdout}\n{result.stderr}"
    yield


@pytest.fixture
async def engine() -> AsyncIterator[Any]:
    async with database_engine(real_database_url()) as engine:
        yield engine


@pytest.mark.asyncio
async def test_F8_payload_that_is_not_json_raises_and_leaves_no_gap(engine) -> None:
    from app.platform.outbox import add_event

    trip_id = uuid.uuid4()
    async with AsyncSession(engine) as session, session.begin():
        with pytest.raises(TypeError):
            await add_event(
                session, trip_id=trip_id, type="expense.changed", payload={"x": object()}
            )
        # The caller catches the error and goes on; the next event is the trip's first.
        assert await add_event(session, trip_id=trip_id, type="expense.changed", payload={}) == 1

    async with engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT trip_seq FROM outbox_events WHERE trip_id = :t ORDER BY id"),
            {"t": trip_id},
        )
        assert [row[0] for row in rows] == [1]
        last = await connection.execute(
            text("SELECT last_seq FROM outbox_trip_seqs WHERE trip_id = :t"), {"t": trip_id}
        )
        assert last.scalar_one() == 1
