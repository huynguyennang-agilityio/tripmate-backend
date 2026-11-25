"""Alembic's environment: migrate the database of the DATABASE_URL setting.

The address is read and checked as the app reads it (`app/platform/config.py`),
so it is never printed and never stored in `alembic.ini`. The models compared by
`alembic check` and `revision --autogenerate` are `Base.metadata`, with every
module's `models.py` loaded by scanning `app/`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterable

from alembic import context
from alembic.operations.ops import MigrationScript
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.platform.config import load_database_url_or_exit
from app.platform.db import Base
from app.platform.modules import import_models

config = context.config

if config.config_file_name is not None:
    from logging.config import fileConfig

    fileConfig(config.config_file_name)

import_models()
target_metadata = Base.metadata


def _database_url() -> str:
    # Only DATABASE_URL is needed here, not REDIS_URL or RELEASE_VERSION. Exits naming
    # the setting, never its value, when it is missing or wrong.
    return load_database_url_or_exit()


def _four_digit_revision(
    _context: MigrationContext, _revision: str | Iterable[str], directives: list[MigrationScript]
) -> None:
    """Number a new revision `%04d` after the current head (`0001`, `0002`, ...)."""
    heads = ScriptDirectory.from_config(config).get_heads()
    numbers = [int(head) for head in heads if head.isdigit()]
    directives[0].rev_id = f"{max(numbers, default=0) + 1:04d}"


def run_migrations_offline() -> None:
    """`--sql`: write the SQL instead of running it."""
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        process_revision_directives=_four_digit_revision,
    )
    with context.begin_transaction():
        context.run_migrations()


def _run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        process_revision_directives=_four_digit_revision,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(_database_url(), poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
