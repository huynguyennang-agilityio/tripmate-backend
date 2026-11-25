"""The PostgreSQL engine. The session and one transaction per request come with card F3."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

# Seconds to wait for PostgreSQL when connecting.
CONNECT_TIMEOUT_SECONDS = 2.0


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
