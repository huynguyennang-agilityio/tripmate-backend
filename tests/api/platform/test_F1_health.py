"""F1 Backend skeleton and health check: case F1-1 (epic-11 spec, section 7.1)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.support import build_app, client_for, real_database_url, real_redis_url


def _string_values(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _string_values(item)]
    if isinstance(value, list):
        return [s for item in value for s in _string_values(item)]
    return []


@pytest.mark.asyncio
async def test_F1_1_health_returns_200_with_release_version(
    set_settings,
) -> None:
    database_url, redis_url = real_database_url(), real_redis_url()
    release = f"f1-release-{uuid.uuid4().hex[:8]}"
    set_settings(DATABASE_URL=database_url, REDIS_URL=redis_url, RELEASE_VERSION=release)

    async with client_for(build_app()) as client:
        response = await client.get("/v1/health")

    assert response.status_code == 200
    # The spec does not name the field, so any string value of the JSON body may carry it.
    assert release in _string_values(response.json())
