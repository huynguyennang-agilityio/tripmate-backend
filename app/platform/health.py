"""`GET /v1/health` (EPIC-11 spec, section 2.7): Render uses it to decide when an instance takes traffic.

`200` with the release version when PostgreSQL and Redis answer, otherwise
`503 server.unavailable`. Both are asked at once, each with a short timeout,
so the answer comes within a few seconds even when a service hangs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass

from fastapi import APIRouter, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncEngine

from app.platform.db import ping_database
from app.platform.errors import AppError
from app.platform.redis_client import ping_redis

CHECK_TIMEOUT_SECONDS = 2.0

router = APIRouter()


@dataclass(frozen=True)
class Resources:
    """What the API process holds open between requests."""

    release_version: str
    engine: AsyncEngine
    redis: Redis


async def _answers(ping: Awaitable[None]) -> bool:
    try:
        async with asyncio.timeout(CHECK_TIMEOUT_SECONDS):
            await ping
    except Exception:  # any failure, a timeout included, means the service does not answer
        return False
    return True


@router.get("/v1/health")
async def health(request: Request) -> dict[str, str]:
    resources: Resources = request.app.state.resources
    database_ok, redis_ok = await asyncio.gather(
        _answers(ping_database(resources.engine)),
        _answers(ping_redis(resources.redis)),
    )
    if not (database_ok and redis_ok):
        raise AppError(503, "server.unavailable")
    return {"status": "ok", "version": resources.release_version}
