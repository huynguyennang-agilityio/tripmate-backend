"""Every integration test starts on a clean database (tests/INTERFACE.md, F3-3).

Before each test under `tests/integration/`, every table of the `public` schema of
`TEST_DATABASE_URL` is emptied, whoever committed its rows (the app or a test's own
connection). Alembic's version table keeps its row. Tables are emptied, not dropped,
so a table a test created stays until that test drops it.
"""

from __future__ import annotations

import asyncio
import os

import pytest
from sqlalchemy import text

from tests.support import TEST_DATABASE_URL, database_engine

# Never emptied: it records which migration the database is at.
KEPT_TABLES = frozenset({"alembic_version"})

# A test that left a transaction open makes the cleaning fail fast instead of hanging.
LOCK_TIMEOUT = "10s"


async def _empty_public_tables(database_url: str) -> None:
    async with database_engine(database_url) as engine, engine.begin() as connection:
        await connection.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
        rows = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = [row[0] for row in rows if row[0] not in KEPT_TABLES]
        if tables:
            names = ", ".join(f'public."{name}"' for name in tables)
            # The names come from the catalog, never from input.
            await connection.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture(autouse=True)
def clean_database() -> None:
    """Empty the test database before the test.

    Without `TEST_DATABASE_URL` there is no database to clean; a test that needs one
    fails on its own (tests/support.py, `real_database_url`).
    """
    database_url = os.environ.get(TEST_DATABASE_URL)
    if database_url:
        asyncio.run(_empty_public_tables(database_url))
