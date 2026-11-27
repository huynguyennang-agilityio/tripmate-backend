"""F6 Rate limits: case F6-6 (epic-11 spec, sections 2.2 "Rate limits", 2.3, and 5.1).

Redis points at a closed local port; PostgreSQL is the real one of the test run. A rate-limited
route then answers `503 server.unavailable` and its handler does not run: a limit that cannot be
counted is not skipped. The interface is in tests/INTERFACE.md, section "F6: Rate limits".
"""

from __future__ import annotations

import pytest

from tests.api.platform.f6_support import (
    PUBLIC_KEYS_SETTING,
    error_code,
    headers,
    new_ip,
    new_private_key,
    public_pem,
    token_for,
    write_probe,
)
from tests.support import build_app, client_for, real_database_url, unreachable_redis_url


@pytest.mark.asyncio
async def test_F6_6_redis_not_answering_is_503_server_unavailable_and_the_request_is_not_let_through(
    set_settings, module_folder, monkeypatch
) -> None:
    set_settings(DATABASE_URL=real_database_url(), REDIS_URL=unreachable_redis_url())
    signing_key = new_private_key()
    monkeypatch.setenv(PUBLIC_KEYS_SETTING, public_pem(signing_key))
    probe = write_probe(module_folder)
    token = token_for(signing_key, "00000000-0000-4000-8000-000000000001")

    async with client_for(build_app()) as client:
        responses = {
            "public route (general ceiling)": await client.get(
                probe.open, headers=headers(new_ip())
            ),
            "signed_in route (general ceiling)": await client.get(
                probe.me, headers=headers(new_ip(), token=token)
            ),
            "route with its own limit": await client.get(
                probe.limited, headers=headers(new_ip(), probe_key="a")
            ),
        }

    for given, response in responses.items():
        assert response.status_code == 503, f"{given}: {response.status_code} {response.text}"
        assert error_code(response) == "server.unavailable", f"{given}: {response.text}"
    assert probe.calls() == [], f"a handler ran although Redis did not answer: {probe.calls()}"
