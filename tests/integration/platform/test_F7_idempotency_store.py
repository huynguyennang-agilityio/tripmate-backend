"""F7 Idempotency keys, level I: F7-1, F7-2, F7-4, F7-5, F7-6, F7-9, F7-10, and the decisions of
the product owner before F7 that no case covers (epic-11 spec, sections 2.4 and 5.1; data-model.md,
section 6.3).

The app runs in process on the real PostgreSQL and Redis; the probe routes of
`tests/integration/platform/f7_support.py` declare `Depends(idempotent)`. The tests read the rows
of `idempotency_keys` and of their probe table through a connection of their own. The interface
is in tests/INTERFACE.md, section "F7: Idempotency keys".
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest

from tests.integration.platform.f7_support import (
    IN_PROGRESS,
    MISMATCH,
    REFUSED,
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
    signed_token,
    stored_markers,
    use_services,
    write_probe,
)
from tests.support import build_app, client_for, unreachable_redis_url

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


def _client(app):
    # A handler that raises comes back as a `500` response instead of being raised here.
    return client_for(app, raise_app_exceptions=False)


# -- F7-1 ----------------------------------------------------------------------------------


async def test_F7_1_create_post_with_a_new_key_runs_once_and_stores_its_status_and_body(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        response = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))

    assert_status(response, 201, "a new key")
    assert_not_replayed(response, "a new key")
    assert probe.calls() == ["records"]
    assert await stored_markers(probe_table) == [response.json()["id"]]
    rows = await key_rows(key)
    assert len(rows) == 1, rows
    row = rows[0]
    assert row["status"] == 201, row
    assert as_json(row["response_body"]) == response.json(), row
    assert row["method"] == "POST", row
    assert row["path"] == probe.path("records"), row
    assert row["request_hash"], row
    assert row["completed_at"] is not None, row
    # Before sign-in the key belongs to the device ID.
    assert (row["device_id"], row["user_id"]) == (device, None), row


# -- F7-2 ----------------------------------------------------------------------------------


async def test_F7_2_same_key_and_body_again_returns_the_stored_response_and_writes_no_second_record(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        first = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))
        again = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))

    assert_status(first, 201, "the first request")
    assert_replay_of(again, first, "the same key and body")
    assert probe.calls() == ["records"], "the handler ran again"
    assert await stored_markers(probe_table) == [first.json()["id"]], "a second record"
    assert len(await key_rows(key)) == 1


# -- F7-4 ----------------------------------------------------------------------------------


async def test_F7_4_same_key_while_the_first_request_still_runs_is_409_in_progress(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        first = asyncio.create_task(
            client.post(probe.path("slow"), json=BODY, headers=headers(device, key))
        )
        try:
            await asyncio.wait_for(probe.started().wait(), timeout=STEP_TIMEOUT)
            second = await asyncio.wait_for(
                client.post(probe.path("slow"), json=BODY, headers=headers(device, key)),
                timeout=STEP_TIMEOUT,
            )
        finally:
            probe.release()
        first_response = await asyncio.wait_for(first, timeout=STEP_TIMEOUT)
        after = await client.post(probe.path("slow"), json=BODY, headers=headers(device, key))

    assert_error(second, 409, IN_PROGRESS, "the same key while the first runs")
    assert_status(first_response, 201, "the first request")
    assert probe.calls() == ["slow"], "the handler ran for the request in flight"
    # Once the first has its response, the same key gets it.
    assert_replay_of(after, first_response, "the same key after the first finished")
    assert await stored_markers(probe_table) == [first_response.json()["id"]]


# -- F7-5 ----------------------------------------------------------------------------------


async def test_F7_5_first_request_fails_with_500_stores_nothing_and_the_retry_runs(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()
    probe.fail("crashes")

    async with _client(build_app()) as client:
        failed = await client.post(probe.path("crashes"), json=BODY, headers=headers(device, key))
        rows_after_failure = await key_rows(key)
        probe.recover("crashes")
        retry = await client.post(probe.path("crashes"), json=BODY, headers=headers(device, key))

    assert_status(failed, 500, "the handler raises")
    assert rows_after_failure == [], "a 5xx left the key's row"
    assert_status(retry, 201, "the retry")
    assert_not_replayed(retry, "the retry")
    assert probe.calls() == ["crashes", "crashes"], "the retry did not run"
    assert await stored_markers(probe_table) == [retry.json()["id"]]
    rows = await key_rows(key)
    assert [row["status"] for row in rows] == [201], rows


async def test_F7_5_first_request_answered_503_by_its_handler_stores_nothing_and_the_retry_runs(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()
    probe.fail("unavailable")

    async with _client(build_app()) as client:
        failed = await client.post(
            probe.path("unavailable"), json=BODY, headers=headers(device, key)
        )
        rows_after_failure = await key_rows(key)
        probe.recover("unavailable")
        retry = await client.post(
            probe.path("unavailable"), json=BODY, headers=headers(device, key)
        )

    assert_error(failed, 503, "server.unavailable", "the handler answers 503")
    assert rows_after_failure == [], "a 5xx left the key's row"
    assert_status(retry, 201, "the retry")
    assert_not_replayed(retry, "the retry")
    assert probe.calls() == ["unavailable", "unavailable"]
    assert await stored_markers(probe_table) == [retry.json()["id"]]


async def test_F7_5_commit_that_fails_stores_neither_record_nor_response_and_the_retry_runs(
    services, module_folder, probe_table
) -> None:
    # The 2xx is stored in the handler's transaction: when that transaction cannot commit, the
    # record and the stored response are both missing, and the answer is a 5xx.
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()
    probe.fail("commit-fails")

    async with _client(build_app()) as client:
        failed = await client.post(
            probe.path("commit-fails"), json=BODY, headers=headers(device, key)
        )
        rows_after_failure = await key_rows(key)
        markers_after_failure = await stored_markers(probe_table)
        probe.recover("commit-fails")
        retry = await client.post(
            probe.path("commit-fails"), json=BODY, headers=headers(device, key)
        )

    assert 500 <= failed.status_code < 600, f"{failed.status_code} {failed.text}"
    assert markers_after_failure == [], "the record of a failed commit was stored"
    assert rows_after_failure == [], "a response was stored for a failed commit"
    assert_status(retry, 201, "the retry")
    assert_not_replayed(retry, "the retry")
    assert probe.calls() == ["commit-fails", "commit-fails"]
    assert await stored_markers(probe_table) == [retry.json()["id"]]


# -- F7-6 ----------------------------------------------------------------------------------


async def test_F7_6_handler_refusal_with_4xx_is_stored_and_returned_for_the_same_key_and_body(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        refused = await client.post(probe.path("refused"), json=BODY, headers=headers(device, key))
        again = await client.post(probe.path("refused"), json=BODY, headers=headers(device, key))

    assert_error(refused, 422, REFUSED, "the handler refuses")
    assert_replay_of(again, refused, "the same key and body after a 4xx")
    assert probe.calls() == ["refused"], "the handler ran again"
    # The 4xx is stored after the handler's changes are rolled back.
    assert await stored_markers(probe_table) == [], "the refused handler's record was stored"
    rows = await key_rows(key)
    assert len(rows) == 1, rows
    assert rows[0]["status"] == 422, rows
    assert as_json(rows[0]["response_body"]) == refused.json(), rows


# -- F7-9 ----------------------------------------------------------------------------------


async def test_F7_9_two_users_with_the_same_key_each_run_and_neither_sees_the_others_response(
    services, module_folder, probe_table
) -> None:
    private_key = services()
    probe = write_probe(module_folder, probe_table)
    # The same device and the same key: only the user differs.
    device, key = new_device(), new_key()
    user_1, user_2 = str(uuid.uuid4()), str(uuid.uuid4())
    token_1, token_2 = signed_token(private_key, user_1), signed_token(private_key, user_2)

    async with _client(build_app()) as client:
        first = await client.post(
            probe.path("mine"), json=BODY, headers=headers(device, key, token=token_1)
        )
        second = await client.post(
            probe.path("mine"), json=BODY, headers=headers(device, key, token=token_2)
        )
        first_again = await client.post(
            probe.path("mine"), json=BODY, headers=headers(device, key, token=token_1)
        )

    assert_status(first, 201, "user 1")
    assert_status(second, 201, "user 2")
    assert_not_replayed(second, "user 2")
    assert first.json()["userId"] == user_1
    assert second.json()["userId"] == user_2, "user 2 got user 1's response"
    assert first.json()["id"] != second.json()["id"]
    assert_replay_of(first_again, first, "user 1 again")
    assert probe.calls() == ["mine", "mine"]
    rows = await key_rows(key)
    assert sorted((row["user_id"], row["device_id"]) for row in rows) == sorted(
        [(user_1, None), (user_2, None)]
    ), rows


# -- F7-10 ---------------------------------------------------------------------------------


async def test_F7_10_before_sign_in_two_device_ids_with_the_same_key_each_run(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device_1, device_2, key = new_device(), new_device(), new_key()

    async with _client(build_app()) as client:
        first = await client.post(probe.path("records"), json=BODY, headers=headers(device_1, key))
        second = await client.post(probe.path("records"), json=BODY, headers=headers(device_2, key))

    assert_status(first, 201, "device 1")
    assert_status(second, 201, "device 2")
    assert_not_replayed(second, "device 2")
    assert first.json()["id"] != second.json()["id"], "device 2 got device 1's response"
    assert probe.calls() == ["records", "records"]
    rows = await key_rows(key)
    assert sorted((row["device_id"], row["user_id"]) for row in rows) == sorted(
        [(device_1, None), (device_2, None)]
    ), rows


# -- Decisions of the product owner before F7 that no case covers -------------------------


async def _make_in_flight(key: str, started_seconds_ago: int) -> None:
    """Turn the key's stored row back into one in flight that started that long ago."""
    await run_sql(
        "UPDATE idempotency_keys SET status = NULL, completed_at = NULL, "
        "created_at = now() - make_interval(secs => :seconds) WHERE key::text = :key",
        key=key,
        seconds=started_seconds_ago,
    )


async def test_F7_extra_in_flight_key_older_than_60_seconds_is_abandoned_and_the_request_runs(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        first = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))
        assert_status(first, 201, "the first request")

        # Started 30 seconds ago with no response: still in flight.
        await _make_in_flight(key, 30)
        recent = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))

        # Started 61 seconds ago with no response: abandoned.
        await _make_in_flight(key, 61)
        abandoned = await client.post(
            probe.path("records"), json=BODY, headers=headers(device, key)
        )

    assert_error(recent, 409, IN_PROGRESS, "a key in flight for 30 seconds")
    assert_status(abandoned, 201, "a key in flight for 61 seconds")
    assert_not_replayed(abandoned, "a key in flight for 61 seconds")
    assert abandoned.json()["id"] != first.json()["id"]
    assert probe.calls() == ["records", "records"]
    rows = await key_rows(key)
    assert len(rows) == 1, rows
    assert rows[0]["status"] == 201, rows
    assert rows[0]["completed_at"] is not None, rows
    assert as_json(rows[0]["response_body"]) == abandoned.json(), rows


async def test_F7_extra_key_older_than_24_hours_is_expired_runs_as_new_and_replaces_its_row(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()
    other_body = {"name": "Taxi", "amount": 30}

    async with _client(build_app()) as client:
        first = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))
        assert_status(first, 201, "the first request")
        await run_sql(
            "UPDATE idempotency_keys SET created_at = now() - interval '25 hours', "
            "completed_at = now() - interval '25 hours' WHERE key::text = :key",
            key=key,
        )
        # Another body: an expired key is not compared with its old request.
        expired = await client.post(
            probe.path("records"), json=other_body, headers=headers(device, key)
        )
        new_again = await client.post(
            probe.path("records"), json=other_body, headers=headers(device, key)
        )
        old_again = await client.post(
            probe.path("records"), json=BODY, headers=headers(device, key)
        )

    assert_status(expired, 201, "a key older than 24 hours")
    assert_not_replayed(expired, "a key older than 24 hours")
    assert expired.json()["name"] == "Taxi"
    assert probe.calls() == ["records", "records"]
    # The row is replaced: the new request is the key's request now.
    assert_replay_of(new_again, expired, "the new body again")
    assert_error(old_again, 409, MISMATCH, "the old body after the row was replaced")
    rows = await key_rows(key)
    assert len(rows) == 1, rows
    assert as_json(rows[0]["response_body"]) == expired.json(), rows
    assert rows[0]["created_at"] > datetime.now(UTC) - timedelta(hours=1), rows


async def test_F7_extra_json_body_is_hashed_with_sorted_keys_and_no_whitespace(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()
    json_type = "application/json"

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


async def test_F7_extra_body_that_is_not_json_is_hashed_as_its_bytes(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()
    plain_text = "text/plain"

    async with _client(build_app()) as client:
        first = await client.post(
            probe.path("text"),
            content=b"pay back  dinner",
            headers=headers(device, key, content_type=plain_text),
        )
        same = await client.post(
            probe.path("text"),
            content=b"pay back  dinner",
            headers=headers(device, key, content_type=plain_text),
        )
        respaced = await client.post(
            probe.path("text"),
            content=b"pay back dinner",
            headers=headers(device, key, content_type=plain_text),
        )

    assert_status(first, 201, "the first request")
    assert_replay_of(same, first, "the same bytes")
    assert_error(respaced, 409, MISMATCH, "other bytes")
    assert probe.calls() == ["text"]


async def test_F7_extra_replay_keeps_location_etag_and_content_type_and_says_it_is_replayed(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        first = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))
        replay = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))

    assert_status(first, 201, "the first request")
    assert_not_replayed(first, "the first request")
    assert first.headers.get("X-Probe-Extra") == "not-stored"
    assert_replay_of(replay, first, "the replay")
    for name in ("Location", "ETag", "Content-Type"):
        assert first.headers.get(name), f"the first response has no {name}"
        assert replay.headers.get(name) == first.headers.get(name), (
            f"{name}: {replay.headers.get(name)!r} is not {first.headers.get(name)!r}"
        )
    # Only Location, ETag, and Content-Type are stored.
    assert "X-Probe-Extra" not in replay.headers
    rows = await key_rows(key)
    stored = {name.lower(): value for name, value in as_json(rows[0]["response_headers"]).items()}
    assert stored.get("location") == first.headers["Location"], stored
    assert stored.get("etag") == first.headers["ETag"], stored
    assert "x-probe-extra" not in stored, stored


async def test_F7_extra_400_for_the_schema_before_the_handler_is_not_stored_and_the_retry_runs(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    async with _client(build_app()) as client:
        invalid = await client.post(
            probe.path("records"), json={"name": "Dinner"}, headers=headers(device, key)
        )
        rows_after_refusal = await key_rows(key)
        valid = await client.post(probe.path("records"), json=BODY, headers=headers(device, key))

    assert_error(invalid, 400, "request.invalid", "a body that fails the schema")
    assert rows_after_refusal == [], "a refusal before the handler was stored"
    assert_status(valid, 201, "the retry with a valid body")
    assert_not_replayed(valid, "the retry with a valid body")
    assert probe.calls() == ["records"]


async def test_F7_extra_401_before_the_handler_is_not_stored_and_the_retry_runs(
    services, module_folder, probe_table
) -> None:
    private_key = services()
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()
    token = signed_token(private_key, str(uuid.uuid4()))

    async with _client(build_app()) as client:
        unsigned = await client.post(probe.path("mine"), json=BODY, headers=headers(device, key))
        rows_after_refusal = await key_rows(key)
        signed = await client.post(
            probe.path("mine"), json=BODY, headers=headers(device, key, token=token)
        )

    assert_error(unsigned, 401, "auth.not_signed_in", "no token")
    assert rows_after_refusal == [], "a 401 was stored"
    assert_status(signed, 201, "the retry signed in")
    assert_not_replayed(signed, "the retry signed in")
    assert probe.calls() == ["mine"]


async def test_F7_extra_429_before_the_handler_is_not_stored_and_the_retry_runs(
    services, module_folder, probe_table
) -> None:
    services()
    probe = write_probe(module_folder, probe_table)
    device, key_1, key_2 = new_device(), new_key(), new_key()

    async with _client(build_app()) as client:
        # The route allows 1 request in 2 seconds.
        allowed = await client.post(
            probe.path("limited"), json=BODY, headers=headers(device, key_1)
        )
        limited = await client.post(
            probe.path("limited"), json=BODY, headers=headers(device, key_2)
        )
        rows_after_refusal = await key_rows(key_2)
        await asyncio.sleep(2.2)
        retry = await client.post(probe.path("limited"), json=BODY, headers=headers(device, key_2))

    assert_status(allowed, 201, "the first request")
    assert_error(limited, 429, "rate.limited", "over the route's limit")
    assert rows_after_refusal == [], "a 429 was stored"
    assert_status(retry, 201, "the retry after the window")
    assert_not_replayed(retry, "the retry after the window")
    assert probe.calls() == ["limited", "limited"]


async def test_F7_extra_503_before_the_handler_is_not_stored_and_the_key_is_released(
    services, module_folder, probe_table
) -> None:
    probe = write_probe(module_folder, probe_table)
    device, key = new_device(), new_key()

    # Redis does not answer: the route's own limit answers 503 before the handler.
    services(REDIS_URL=unreachable_redis_url())
    async with _client(build_app()) as client:
        down = await client.post(probe.path("limited"), json=BODY, headers=headers(device, key))
    rows_after_refusal = await key_rows(key)

    services()
    async with _client(build_app()) as client:
        retry = await client.post(probe.path("limited"), json=BODY, headers=headers(device, key))

    assert_error(down, 503, "server.unavailable", "Redis does not answer")
    assert rows_after_refusal == [], "a 503 was stored"
    assert_status(retry, 201, "the retry with Redis up")
    assert_not_replayed(retry, "the retry with Redis up")
    assert probe.calls() == ["limited"]
