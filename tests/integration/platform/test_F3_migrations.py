"""F3 Database session and migrations: cases F3-4 and F3-5 (epic-11 spec, section 4.2).

Alembic is run as `python -m alembic ...` from the repository root, against a new, empty
database of the test's own (tests/INTERFACE.md, section "Migrations").
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from tests.support import database_engine, empty_database, run_alembic


def _output(result) -> str:
    return (
        f"exit code {result.returncode}\n--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}"
    )


async def _versions(database_url: str) -> list[str]:
    async with database_engine(database_url) as engine, engine.connect() as connection:
        exists = await connection.scalar(text("SELECT to_regclass('alembic_version')"))
        if exists is None:
            return []
        rows = await connection.execute(text("SELECT version_num FROM alembic_version"))
        return [row[0] for row in rows]


@pytest.mark.asyncio
async def test_F3_4_migrating_an_empty_database_up_to_head_then_down_to_base_succeeds() -> None:
    async with empty_database() as database_url:
        up = run_alembic(database_url, "upgrade", "head")
        assert up.returncode == 0, _output(up)
        # The first migration exists, so the database is now at one head revision.
        assert len(await _versions(database_url)) == 1

        down = run_alembic(database_url, "downgrade", "base")
        assert down.returncode == 0, _output(down)
        assert await _versions(database_url) == []


MODELS_SOURCE = """\
from sqlalchemy.orm import Mapped, mapped_column

from app.platform.db import Base


class F3Probe(Base):
    __tablename__ = "{table}"

    id: Mapped[int] = mapped_column(primary_key=True)
"""


@pytest.mark.asyncio
async def test_F3_5_model_that_differs_from_the_migrations_makes_the_migration_check_fail(
    module_folder,
) -> None:
    async with empty_database() as database_url:
        up = run_alembic(database_url, "upgrade", "head")
        assert up.returncode == 0, _output(up)

        # The models and the migrations match: the check passes.
        matching = run_alembic(database_url, "check")
        assert matching.returncode == 0, _output(matching)

        # A module's model adds a table that no migration creates.
        table = f"f3probe_{uuid.uuid4().hex[:8]}"
        module_folder({"__init__.py": "", "models.py": MODELS_SOURCE.format(table=table)})

        differing = run_alembic(database_url, "check")
        assert differing.returncode != 0, _output(differing)
        assert table in differing.stdout + differing.stderr, _output(differing)
