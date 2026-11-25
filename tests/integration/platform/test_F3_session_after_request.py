"""F3 review round 2, R2-1: the request's session is refused on every use after the request.

A route hands the request's session to a `BackgroundTasks` job. The job runs after the
response, so the session is closed: its first use raises `SessionClosedError`, a use after
catching that error raises again, no row is stored, and no connection of the database is
left "idle in transaction".
"""

from __future__ import annotations

import asyncio
import importlib
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

API_SOURCE = """\
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.platform.db import get_session

router = APIRouter()


class ProbeBase(DeclarativeBase):
    pass


class Probe(ProbeBase):
    __tablename__ = "{table}"

    marker: Mapped[str] = mapped_column(primary_key=True)

INSERT = text("INSERT INTO {table} (marker) VALUES (:marker)")

# What each attempt of the job did: the name of the error it raised, or "ok".
OUTCOMES: list[str] = []


async def _attempt(label: str, call) -> None:
    try:
        await call()
    except Exception as error:
        OUTCOMES.append(f"{{label}}:{{type(error).__name__}}")
    else:
        OUTCOMES.append(f"{{label}}:ok")


async def late_job(session: AsyncSession, marker: str) -> None:
    async def execute() -> None:
        await session.execute(INSERT, {{"marker": marker}})

    async def connection() -> None:
        await session.connection()

    async def get() -> None:
        await session.get(Probe, "x")

    async def flush() -> None:
        await session.flush()

    async def begin() -> None:
        await session.begin()

    for label, call in [
        ("execute-1", execute),
        ("execute-2", execute),
        ("connection", connection),
        ("get", get),
        ("flush", flush),
        ("begin-1", begin),
        ("begin-2", begin),
        ("execute-3", execute),
    ]:
        await _attempt(label, call)


@router.post("/{segment}/late")
async def late(
    marker: str,
    background: BackgroundTasks,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> dict[str, str]:
    background.add_task(late_job, session, marker)
    return {{"queued": marker}}
"""


@pytest.fixture
async def probe_table() -> AsyncIterator[str]:
    name = f"f3probe_{uuid.uuid4().hex[:8]}"
    async with database_engine(real_database_url()) as engine:
        async with engine.begin() as connection:
            await connection.execute(text(f"CREATE TABLE {name} (marker text NOT NULL)"))
        try:
            yield name
        finally:
            async with engine.begin() as connection:
                await connection.execute(text(f"DROP TABLE IF EXISTS {name}"))


async def _idle_in_transaction() -> int:
    query = text(
        "SELECT count(*) FROM pg_stat_activity WHERE datname = current_database() "
        "AND pid <> pg_backend_pid() AND state LIKE 'idle in transaction%'"
    )
    async with database_engine(real_database_url()) as engine, engine.connect() as connection:
        return int((await connection.execute(query)).scalar_one())


@pytest.mark.asyncio
async def test_F3_1_session_used_after_the_request_is_refused_every_time(
    set_settings, module_folder, probe_table
) -> None:
    set_settings(DATABASE_URL=real_database_url())
    name = module_folder({"__init__.py": ""})
    segment = name.replace("_", "-")
    source = API_SOURCE.format(table=probe_table, segment=segment)
    (APP_DIR / name / "api.py").write_text(source, encoding="utf-8")
    importlib.invalidate_caches()
    marker = f"f3-1-{uuid.uuid4().hex[:8]}"

    async with client_for(build_app()) as client:
        response = await client.post(
            f"/v1/{segment}/late",
            params={"marker": marker},
            headers={"X-Device-Id": str(uuid.uuid4())},
        )
        assert response.status_code == 200, f"{response.status_code} {response.text}"

        # The job ran after the response; the connections get a moment to settle.
        idle = await _idle_in_transaction()
        for _ in range(20):
            if idle == 0:
                break
            await asyncio.sleep(0.1)
            idle = await _idle_in_transaction()

    outcomes = importlib.import_module(f"app.{name}.api").OUTCOMES
    assert outcomes == [
        "execute-1:SessionClosedError",
        "execute-2:SessionClosedError",
        "connection:SessionClosedError",
        "get:SessionClosedError",
        "flush:ok",  # nothing pending, so nothing touches the database
        "begin-1:SessionClosedError",
        "begin-2:SessionClosedError",
        "execute-3:SessionClosedError",
    ]
    assert idle == 0, f"{idle} connection(s) left idle in transaction"

    async with database_engine(real_database_url()) as engine, engine.connect() as connection:
        rows = await connection.execute(text(f"SELECT marker FROM {probe_table}"))  # noqa: S608
        assert list(rows) == []
