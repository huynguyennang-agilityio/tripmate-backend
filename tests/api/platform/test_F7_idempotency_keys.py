"""F7 Idempotency keys, level A: F7-1, F7-2, F7-3, F7-7, F7-8, and a key on another method or path
(epic-11 spec, section 2.4; api-design.md, section 2, row "Idempotency").

The app runs in process on the real PostgreSQL and Redis, and the tests read only its answers
and which handlers ran. The probe routes are those of `tests/integration/platform/f7_support.py`.
The interface is in tests/INTERFACE.md, section "F7: Idempotency keys".
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from tests.integration.platform.f7_support import (
    KEY_REQUIRED,
    MISMATCH,
    REQUEST_INVALID,
    assert_error,
    assert_not_replayed,
    assert_replay_of,
    assert_status,
    create_probe_table,
    drop_probe_table,
    headers,
    migrate,
    new_device,
    new_key,
    use_services,
    write_probe,
)
from tests.support import build_app, client_for

BODY = {"name": "Dinner", "amount": 120}


@pytest.fixture(scope="module", autouse=True)
def migrated_database() -> None:
    migrate()


@pytest.fixture
async def probe_table() -> AsyncIterator[str]:
    name = await create_probe_table()
    try:
        yield name
    finally:
        await drop_probe_table(name)


@pytest.fixture
def probe(set_settings, monkeypatch: pytest.MonkeyPatch, module_folder, probe_table):
    use_services(set_settings, monkeypatch)
    return write_probe(module_folder, probe_table)


# -- F7-1 ----------------------------------------------------------------------------------


async def test_F7_1_create_post_with_a_new_key_runs_once_and_answers_its_response(probe) -> None:
    async with client_for(build_app()) as client:
        response = await client.post(
            probe.path("records"), json=BODY, headers=headers(new_device(), new_key())
        )

    assert_status(response, 201, "a new key")
    assert_not_replayed(response, "a new key")
    body = response.json()
    assert (body["name"], body["amount"]) == ("Dinner", "120"), body
    assert probe.calls() == ["records"]


# -- F7-2 ----------------------------------------------------------------------------------


async def test_F7_2_same_key_and_body_again_answers_the_stored_response_without_running_again(
    probe,
) -> None:
    device, key = new_device(), new_key()

    async with client_for(build_app()) as client:
        first = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))
        again = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))

    assert_status(first, 201, "the first request")
    assert_replay_of(again, first, "the same key and body")
    assert probe.calls() == ["records"], "the handler ran a second time"


# -- F7-3 ----------------------------------------------------------------------------------


async def test_F7_3_same_key_with_a_different_body_is_409_mismatch(probe) -> None:
    device, key = new_device(), new_key()

    async with client_for(build_app()) as client:
        first = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))
        other = await client.post(
            probe.path("records"),
            json={"name": "Dinner", "amount": 121},
            headers=headers(device, key),
        )

    assert_status(first, 201, "the first request")
    assert_error(other, 409, MISMATCH, "the same key with another body")
    assert probe.calls() == ["records"]


# -- F7-7 ----------------------------------------------------------------------------------


async def test_F7_7_create_post_with_no_key_is_400_idempotency_key_required(probe) -> None:
    async with client_for(build_app()) as client:
        response = await client.post(
            probe.path("records"), json=BODY, headers=headers(new_device(), None)
        )

    assert_error(response, 400, KEY_REQUIRED, "no Idempotency-Key")
    assert probe.calls() == [], "the handler ran without a key"


# -- F7-8 ----------------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["not-a-uuid", "12345"])
async def test_F7_8_key_that_is_not_a_uuid_is_400_request_invalid(probe, key: str) -> None:
    async with client_for(build_app()) as client:
        response = await client.post(
            probe.path("records"), json=BODY, headers=headers(new_device(), key)
        )

    assert_error(response, 400, REQUEST_INVALID, f"Idempotency-Key {key!r}")
    assert probe.calls() == [], "the handler ran with a key that is not a UUID"


# -- Decision: the same key on another method or path --------------------------------------


async def test_F7_extra_same_key_on_another_path_or_method_is_409_mismatch(probe) -> None:
    device, key = new_device(), new_key()

    async with client_for(build_app()) as client:
        first = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))
        other_path = await client.post(
            probe.path("others"), json=BODY, headers=headers(device, key)
        )
        other_method = await client.put(
            probe.path("records"), json=BODY, headers=headers(device, key)
        )

    assert_status(first, 201, "the first request")
    assert_error(other_path, 409, MISMATCH, "the same key and body on another path")
    assert_error(other_method, 409, MISMATCH, "the same key and body with another method")
    assert probe.calls() == ["records"]
