"""F3 Database session and migrations: case F3-3 (task EPIC-11, F3 scope).

Every integration test gets a clean database (tests/INTERFACE.md, section "A clean
database for each integration test"). The first test commits a row in a table of its
own; the second, run right after it, finds none of that row. The two run in file order.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.support import database_engine, real_database_url

# The table names below are made by the tests, never taken from input (hence the S608 noqa).

# Shared between the two tests of this file, so the second can find the first one's table.
_FIRST_TEST: dict[str, str] = {}


@pytest.fixture(scope="module", autouse=True)
def _drop_the_probe_table_at_the_end() -> object:
    yield None
    table = _FIRST_TEST.get("table")
    if table is None:
        return
    import asyncio

    async def _drop() -> None:
        async with database_engine(real_database_url()) as engine, engine.begin() as connection:
            await connection.execute(text(f"DROP TABLE IF EXISTS {table}"))

    asyncio.run(_drop())


@pytest.mark.asyncio
async def test_F3_3_first_test_commits_a_row() -> None:
    table = f"f3probe_{uuid.uuid4().hex[:8]}"
    marker = f"f3-3-{uuid.uuid4().hex[:8]}"
    _FIRST_TEST.update(table=table, marker=marker)

    async with database_engine(real_database_url()) as engine:
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE TABLE {table} (marker text NOT NULL)"))
            insert = text(f"INSERT INTO {table} (marker) VALUES (:marker)")  # noqa: S608
            await connection.execute(insert, {"marker": marker})
        async with engine.connect() as connection:
            rows = await connection.execute(text(f"SELECT marker FROM {table}"))  # noqa: S608
            assert [row[0] for row in rows] == [marker]


@pytest.mark.asyncio
async def test_F3_3_second_test_sees_none_of_the_first_tests_rows() -> None:
    assert "table" in _FIRST_TEST, (
        "run this test right after test_F3_3_first_test_commits_a_row, in the same session"
    )
    table = _FIRST_TEST["table"]

    async with database_engine(real_database_url()) as engine, engine.connect() as connection:
        exists = await connection.scalar(text("SELECT to_regclass(:table)"), {"table": table})
        # A clean database may have dropped the table or only emptied it: both leave no row.
        select = text(f"SELECT * FROM {table}")  # noqa: S608
        rows = [] if exists is None else list(await connection.execute(select))

    assert rows == [], f"the second test found the first one's rows in {table}: {rows}"
