"""F1 Backend skeleton and health check: cases F1-2 and F1-3 (epic-11 spec, section 7.1).

The service that is down points at a closed local port; the other one is the
real service of the test run.
"""

from __future__ import annotations

import pytest

from tests.support import (
    build_app,
    client_for,
    real_database_url,
    real_redis_url,
    unreachable_database_url,
    unreachable_redis_url,
)


@pytest.mark.asyncio
async def test_F1_2_health_returns_503_when_postgresql_does_not_answer(
    set_settings,
) -> None:
    set_settings(DATABASE_URL=unreachable_database_url(), REDIS_URL=real_redis_url())

    async with client_for(build_app()) as client:
        response = await client.get("/v1/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "server.unavailable"


@pytest.mark.asyncio
async def test_F1_3_health_returns_503_when_redis_does_not_answer(
    set_settings,
) -> None:
    set_settings(DATABASE_URL=real_database_url(), REDIS_URL=unreachable_redis_url())

    async with client_for(build_app()) as client:
        response = await client.get("/v1/health")

    assert response.status_code == 503
    assert response.json()["error"]["code"] == "server.unavailable"
