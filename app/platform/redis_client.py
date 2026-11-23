"""The Redis client shared by the platform: queue, rate limits, and the outbox channels."""

from __future__ import annotations

from redis.asyncio import Redis

# Seconds to wait for Redis when connecting and for each answer.
SOCKET_TIMEOUT_SECONDS = 2.0


def create_redis(redis_url: str) -> Redis:
    """A client for the app. It connects lazily, so the app starts when Redis is down."""
    return Redis.from_url(
        redis_url,
        socket_connect_timeout=SOCKET_TIMEOUT_SECONDS,
        socket_timeout=SOCKET_TIMEOUT_SECONDS,
    )


async def ping_redis(redis: Redis) -> None:
    """Send PING; raises when Redis does not answer."""
    await redis.ping()
