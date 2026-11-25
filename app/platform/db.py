"""PostgreSQL: the engine, the models' base, and one transaction per request (EPIC-11 spec, 2.2).

A handler asks for a session with `Depends(get_session)` and never commits: the
platform commits when the handler returns and rolls back when it raises, so the
change, its Audit log entries, and its outbox events are stored together or not
at all.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, FastAPI, Request
from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

# Seconds to wait for PostgreSQL when connecting.
CONNECT_TIMEOUT_SECONDS = 2.0

# Constraint names Alembic can compare and drop by name, the same on every machine.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_N_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """The declarative base of every model; each module's `models.py` subclasses it."""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(database_url: str) -> AsyncEngine:
    """An engine for the app. It connects lazily, so the app starts when PostgreSQL is down."""
    return create_async_engine(
        database_url,
        pool_pre_ping=True,
        connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
    )


async def ping_database(engine: AsyncEngine) -> None:
    """Run `SELECT 1`; raises when PostgreSQL does not answer."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


def install_sessions(app: FastAPI, engine: AsyncEngine) -> None:
    """Make `get_session` hand out sessions on `engine` (called at the app's start-up)."""
    # Objects stay readable after the commit, which happens once the handler returned.
    app.state.session_factory = async_sessionmaker(engine, expire_on_commit=False)


async def _session_in_transaction(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    # `begin()` commits when the block ends normally and rolls back when the
    # handler's exception is thrown in here.
    async with factory() as session, session.begin():
        yield session


async def get_session(
    # Scope "function": the transaction ends right after the handler returns, before
    # the response is sent, so a failed commit is answered as an error, not a `200`.
    session: Annotated[AsyncSession, Depends(_session_in_transaction, scope="function")],
) -> AsyncSession:
    """FastAPI dependency: the request's session, in its one transaction."""
    return session
