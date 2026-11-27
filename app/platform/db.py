"""PostgreSQL: the engine, the models' base, and one transaction per request (EPIC-11 spec, 2.2).

A handler asks for a session with `Depends(get_session)` and never commits: the
platform commits when the handler returns and rolls back when it raises, so the
change, its Audit log entries, and its outbox events are stored together or not
at all.

The session lives for the request only. Work that outlives the response (a
`BackgroundTasks` function, a task started with `asyncio.create_task`) never
uses it: it goes through the outbox (spec 2.5), whose events the worker runs in
a session of their own. A session used after the request ended raises
`SessionClosedError` instead of opening a transaction nobody ends.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from sqlalchemy import MetaData, event, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase, ORMExecuteState, Session, SessionTransaction

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
        hide_parameters=True,
        connect_args={"timeout": CONNECT_TIMEOUT_SECONDS},
    )


async def ping_database(engine: AsyncEngine) -> None:
    """Run `SELECT 1`; raises when PostgreSQL does not answer."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))


def install_sessions(app: FastAPI, engine: AsyncEngine) -> None:
    """Make `get_session` hand out sessions on `engine` (called at the app's start-up)."""
    # Objects stay readable after the commit, which happens once the handler returned.
    app.state.session_factory = async_sessionmaker(
        engine, expire_on_commit=False, sync_session_class=_RequestSession
    )


class SessionCommitError(RuntimeError):
    """A handler committed the session itself; only the platform commits."""


class SessionClosedError(RuntimeError):
    """The request's session was used after the request ended."""


# Keys of `Session.info` that the guards below read and write.
_PLATFORM_COMMIT = "tripmate.platform_commit"
_STRAY_COMMIT = "tripmate.stray_commit"
_REQUEST_ENDED = "tripmate.request_ended"


def _closed_error() -> SessionClosedError:
    return SessionClosedError(
        "The request's session is closed. Work after the response goes through "
        "the outbox, never the request's session"
    )


def _guard(session: AsyncSession) -> None:
    """Make the session refuse a commit by a handler and any use after the request.

    After the request every use raises, not only the first: a statement (`execute`,
    `get`, ...) is refused before it reaches the database, `begin` and `connection`
    are refused too, and no connection is ever checked out again.
    """
    sync_session = session.sync_session

    def before_commit(committing: Session) -> None:
        if not committing.info.get(_PLATFORM_COMMIT):
            # Raised before anything is written. The mark makes the platform roll the
            # whole request back even when the handler catches this error.
            committing.info[_STRAY_COMMIT] = True
            raise SessionCommitError(
                "A handler never commits: the platform commits when the handler returns"
            )

    def do_orm_execute(state: ORMExecuteState) -> None:
        if state.session.info.get(_REQUEST_ENDED):
            raise _closed_error()

    event.listen(sync_session, "before_commit", before_commit)
    event.listen(sync_session, "do_orm_execute", do_orm_execute)


class _RequestSession(Session):
    """The sync session behind the request's `AsyncSession`.

    Once the request ended it takes no transaction and gives no connection, so a
    background task that catches `SessionClosedError` and tries again fails again.
    """

    def _check_open(self) -> None:
        if self.info.get(_REQUEST_ENDED):
            raise _closed_error()

    def get_bind(self, *args: Any, **kwargs: Any) -> Any:
        self._check_open()
        return super().get_bind(*args, **kwargs)

    def begin(self, nested: bool = False) -> SessionTransaction:
        self._check_open()
        return super().begin(nested=nested)

    def begin_nested(self) -> SessionTransaction:
        self._check_open()
        return super().begin_nested()


async def _session_in_transaction(request: Request) -> AsyncIterator[AsyncSession]:
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    async with factory() as session:
        _guard(session)
        await session.begin()
        try:
            yield session
            if session.info.get(_STRAY_COMMIT):
                raise SessionCommitError("A handler committed the session; nothing was stored")
            # The handler's exception is thrown into the `yield`, so a raise rolls back.
            session.info[_PLATFORM_COMMIT] = True
            await session.commit()
        except BaseException:
            await session.rollback()
            raise
        finally:
            await session.close()
            session.info[_REQUEST_ENDED] = True


async def get_session(
    # Scope "function": the transaction ends right after the handler returns, before
    # the response is sent, so a failed commit is answered as an error, not a `200`.
    session: Annotated[AsyncSession, Depends(_session_in_transaction, scope="function")],
) -> AsyncSession:
    """FastAPI dependency: the request's session, in its one transaction."""
    return session
