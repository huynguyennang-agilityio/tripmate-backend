"""F7 tests of the code agent's own choices (not locked): a `4xx` the handler returns, a `+json`
body, and a claim taken over after 60 seconds (epic-11 spec, section 2.4).

Probe routes: `tests/integration/platform/f7_code_choices_support.py`.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

import pytest

from tests.integration.platform.f7_code_choices_support import write_choices_probe
from tests.integration.platform.f7_support import (
    IN_PROGRESS,
    MISMATCH,
    STEP_TIMEOUT,
    as_json,
    assert_error,
    assert_not_replayed,
    assert_replay_of,
    assert_status,
    create_probe_table,
    drop_probe_table,
    headers,
    key_rows,
    migrate,
    new_device,
    new_key,
    run_sql,
    stored_markers,
    use_services,
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
def services(set_settings, monkeypatch: pytest.MonkeyPatch):
    def _use(**overrides: str):
        return use_services(set_settings, monkeypatch, **overrides)

    return _use


def write_probe(module_folder, table):
    return write_choices_probe(module_folder, table)


def _client(app):
    return client_for(app, raise_app_exceptions=False)


def _assert_byte_for_byte_replay(replay, first, what: str) -> None:
    assert_replay_of(replay, first, what)
    assert replay.content == first.content, f"{what}: {replay.content!r} is not {first.content!r}"


async def test_F7_extra_4xx_response_returned_by_the_handler_is_rolled_back_stored_and_replayed(
    services, module_folder, probe_table
) -> None:
    # The handler returns (does not raise) a 409 response after writing a record.
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        first = await client.post(
            probe.path("returns-4xx"), json=BODY, headers=headers(device, key)
        )
        again = await client.post(
            probe.path("returns-4xx"), json=BODY, headers=headers(device, key)
        )

    assert_status(first, 409, "the handler returns 409")
    assert first.json()["reason"] == "returned", first.text
    assert_not_replayed(first, "the handler returns 409")
    _assert_byte_for_byte_replay(again, first, "the same key and body after the returned 409")
    assert probe.calls() == ["returns-4xx"], "the handler ran again"
    assert await stored_markers(probe_table) == [], "the record of a returned 4xx was stored"
    rows = await key_rows(key)
    assert len(rows) == 1, rows
    assert rows[0]["status"] == 409, rows
    assert as_json(rows[0]["response_body"]) == first.json(), rows


async def test_F7_extra_plus_json_body_is_hashed_as_json_with_sorted_keys_and_no_whitespace(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()
    json_type = "application/merge-patch+json"

    async with _client(build_app()) as client:
        first = await client.post(
            probe.path("records"),
            content=b'{"name":"Dinner","amount":120}',
            headers=headers(device, key, content_type=json_type),
        )
        reordered = await client.post(
            probe.path("records"),
            content=b'{ "amount": 120,\n  "name": "Dinner" }',
            headers=headers(device, key, content_type=json_type),
        )
        changed = await client.post(
            probe.path("records"),
            content=b'{"name":"Dinner","amount":121}',
            headers=headers(device, key, content_type=json_type),
        )

    assert_status(first, 201, "the first request")
    assert_replay_of(reordered, first, "the same fields in another order and spacing")
    assert_error(changed, 409, MISMATCH, "another value")
    assert probe.calls() == ["records"]
    assert await stored_markers(probe_table) == [first.json()["id"]]


async def test_F7_extra_request_whose_claim_was_taken_over_answers_409_and_its_work_is_rolled_back(
    services, module_folder, probe_table
) -> None:
    # The first request claims the key and waits; its claim is made 61 seconds old, so a second
    # request takes it over and finishes. When the first then finishes with 2xx, its record is
    # rolled back, it answers 409 in progress, and the second's row is left as it is.
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        old = asyncio.create_task(
            client.post(probe.path("slow-once"), json=BODY, headers=headers(device, key))
        )
        try:
            await asyncio.wait_for(probe.started().wait(), timeout=STEP_TIMEOUT)
            await run_sql(
                "UPDATE idempotency_keys SET created_at = now() - interval '61 seconds' "
                "WHERE key::text = :key",
                key=key,
            )
            new = await asyncio.wait_for(
                client.post(probe.path("slow-once"), json=BODY, headers=headers(device, key)),
                timeout=STEP_TIMEOUT,
            )
            rows_of_new = await key_rows(key)
        finally:
            probe.release()
        old_response = await asyncio.wait_for(old, timeout=STEP_TIMEOUT)
        after = await client.post(probe.path("slow-once"), json=BODY, headers=headers(device, key))

    assert_status(new, 201, "the request that took the claim over")
    assert_not_replayed(new, "the request that took the claim over")
    assert_error(old_response, 409, IN_PROGRESS, "the request whose claim was taken over")
    assert probe.calls() == ["slow-once", "slow-once"]
    assert await stored_markers(probe_table) == [new.json()["id"]], "the old request's record"
    rows = await key_rows(key)
    assert rows == rows_of_new, "the old request changed the new owner's row"
    assert len(rows) == 1, rows
    assert rows[0]["status"] == 201, rows
    assert as_json(rows[0]["response_body"]) == new.json(), rows
    assert_replay_of(after, new, "the same key after both finished")


async def test_F7_extra_3xx_response_is_stored_in_the_handlers_transaction_and_replayed(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        first = await client.post(probe.path("redirects"), json=BODY, headers=headers(device, key))
        again = await client.post(probe.path("redirects"), json=BODY, headers=headers(device, key))

    assert_status(first, 303, "the handler returns 303")
    assert again.status_code == 303 and again.headers.get("Idempotent-Replayed") == "true"
    assert again.headers["Location"] == first.headers["Location"]
    assert probe.calls() == ["redirects"]
    assert len(await stored_markers(probe_table)) == 1, "the record of a 3xx was not committed"
    rows = await key_rows(key)
    assert [row["status"] for row in rows] == [303], rows


async def test_F7_extra_two_first_requests_with_a_new_key_at_once_run_the_handler_once(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        responses = await asyncio.gather(
            *[
                client.post(probe.path("records"), json=BODY, headers=headers(device, key))
                for _ in range(2)
            ]
        )

    by_status = sorted(response.status_code for response in responses)
    assert by_status in ([201, 201], [201, 409]), [r.text for r in responses]
    for response in responses:
        if response.status_code == 409:
            assert_error(response, 409, IN_PROGRESS, "the second request at once")
        elif response.headers.get("Idempotent-Replayed"):
            assert response.json()["id"] in await stored_markers(probe_table)
    assert probe.calls() == ["records"], "the handler ran twice for one key"
    assert len(await stored_markers(probe_table)) == 1
    assert len(await key_rows(key)) == 1


async def test_F7_extra_response_longer_than_the_limit_is_passed_through_unstored_and_committed(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        first = await client.post(probe.path("big"), json=BODY, headers=headers(device, key))
        rows_after = await key_rows(key)
        again = await client.post(probe.path("big"), json=BODY, headers=headers(device, key))

    assert_status(first, 201, "a response over the limit")
    assert len(first.json()["padding"]) > 1024 * 1024
    assert await stored_markers(probe_table) == sorted([first.json()["id"], again.json()["id"]]), (
        "the handler's transaction was not committed, or the retry did not run"
    )
    assert rows_after == [], "a response over the limit was stored"
    assert_not_replayed(again, "the retry of a response that was not stored")
    assert probe.calls() == ["big", "big"]


async def test_F7_extra_handler_that_uses_no_session_stores_its_response_and_replays_it(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        first = await client.post(probe.path("no-session"), json=BODY, headers=headers(device, key))
        again = await client.post(probe.path("no-session"), json=BODY, headers=headers(device, key))

    assert_status(first, 201, "a handler with no session")
    assert_replay_of(again, first, "the same key and body")
    assert probe.calls() == ["no-session"]
