"""F3 Database session and migrations: cases F3-1 and F3-2 (epic-11 spec, section 2.2).

A handler in a temporary module writes a row through the platform's session and never
commits itself; the platform stores the change when the handler returns and nothing when
it raises. The rows are read back through a connection of the test's own.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text

from tests.support import (
    APP_DIR,
    build_app,
    client_for,
    database_engine,
    real_database_url,
)

# tests/INTERFACE.md, section "Database session".
API_SOURCE = """\
from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db import get_session

router = APIRouter()

INSERT = text("INSERT INTO {table} (marker) VALUES (:marker)")


@router.post("/{segment}/returns")
async def write_and_return(
    marker: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    await session.execute(INSERT, {{"marker": marker}})
    return {{"written": marker}}


@router.post("/{segment}/raises")
async def write_and_raise(
    marker: str, session: Annotated[AsyncSession, Depends(get_session)]
) -> dict[str, str]:
    await session.execute(INSERT, {{"marker": marker}})
    raise RuntimeError("F3-2 probe: the handler raises after writing")
"""


@pytest.fixture
async def probe_table() -> AsyncIterator[str]:
    """A table of the test's own on the real PostgreSQL, dropped after the test."""
    name = f"f3probe_{uuid.uuid4().hex[:8]}"
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
async def test_F3_1_handler_that_writes_a_row_and_returns_stores_the_row(
    set_settings, module_folder, probe_table
) -> None:
    set_settings(DATABASE_URL=real_database_url())
    path = _probe_module(module_folder, probe_table)
    marker = f"f3-1-{uuid.uuid4().hex[:8]}"

    async with client_for(build_app()) as client:
        response = await client.post(
            f"{path}/returns", params={"marker": marker}, headers=_headers()
        )
    assert response.status_code == 200, f"{response.status_code} {response.text}"

    assert await _stored_markers(probe_table) == [marker]


@pytest.mark.asyncio
async def test_F3_2_handler_that_writes_a_row_then_raises_stores_no_row(
    set_settings, module_folder, probe_table
) -> None:
    set_settings(DATABASE_URL=real_database_url())
    path = _probe_module(module_folder, probe_table)
    marker = f"f3-2-{uuid.uuid4().hex[:8]}"

    app = build_app()
    async with client_for(app, raise_app_exceptions=False) as client:
        # The same route shape stores the row when the handler returns: the write works.
        control = await client.post(
            f"{path}/returns", params={"marker": f"{marker}-control"}, headers=_headers()
        )
        assert control.status_code == 200, f"{control.status_code} {control.text}"
        await client.post(f"{path}/raises", params={"marker": marker}, headers=_headers())

    assert await _stored_markers(probe_table) == [f"{marker}-control"]
