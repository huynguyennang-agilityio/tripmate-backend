"""F7 (code agent's own tests): the rules of the idempotency keys, with no PostgreSQL or Redis.

Covers: the key's form, the request hash (sorted compact JSON, other bodies as bytes, method and
path included), the decision on an earlier row at its time limits, and the headers that are
stored.
"""

from __future__ import annotations

import pytest

from app.platform.idempotency import (
    ABANDONED_AFTER_SECONDS,
    EXPIRED_AFTER_SECONDS,
    Verdict,
    judge,
    parse_key,
    request_hash,
    stored_headers,
)

JSON = "application/json"
KEY = "6f1c2b1e-8f0e-4c55-9d53-0b8a2d6f7a11"


@pytest.mark.parametrize(
    "value", ["not-a-uuid", "12345", "", KEY.replace("-", ""), "{" + KEY + "}"]
)
def test_F7_unit_a_key_that_is_not_a_uuid_with_hyphens_is_refused(value: str) -> None:
    assert parse_key(value) is None


def test_F7_unit_a_uuid_key_is_parsed_in_any_case() -> None:
    assert str(parse_key(KEY)) == KEY
    assert str(parse_key(KEY.upper())) == KEY


def test_F7_unit_json_bodies_with_the_same_fields_have_the_same_hash() -> None:
    compact = request_hash("POST", "/v1/a", JSON, b'{"a":1,"b":2}')
    spaced = request_hash("POST", "/v1/a", JSON + "; charset=utf-8", b'{ "b": 2,\n "a": 1 }')
    assert compact == spaced
    assert compact != request_hash("POST", "/v1/a", JSON, b'{"a":1,"b":3}')


def test_F7_unit_a_body_that_is_not_json_is_hashed_as_its_bytes() -> None:
    plain = request_hash("POST", "/v1/a", "text/plain", b"a  b")
    assert plain == request_hash("POST", "/v1/a", "text/plain", b"a  b")
    assert plain != request_hash("POST", "/v1/a", "text/plain", b"a b")
    # JSON text is not normalised unless it says it is JSON.
    assert request_hash("POST", "/v1/a", "text/plain", b'{"a":1}') != request_hash(
        "POST", "/v1/a", "text/plain", b'{ "a": 1 }'
    )


def test_F7_unit_a_body_that_says_json_but_is_not_is_hashed_as_its_bytes() -> None:
    assert request_hash("POST", "/v1/a", JSON, b"{oops") != request_hash(
        "POST", "/v1/a", JSON, b"{oops "
    )


def test_F7_unit_method_and_path_are_part_of_the_hash() -> None:
    base = request_hash("POST", "/v1/a", JSON, b"{}")
    assert base != request_hash("PUT", "/v1/a", JSON, b"{}")
    assert base != request_hash("POST", "/v1/b", JSON, b"{}")
    # The parts cannot run into each other.
    assert request_hash("POST", "/v1/a", "", b"b") != request_hash("POST", "/v1/ab", "", b"")


def test_F7_unit_a_finished_row_with_the_same_hash_is_replayed_and_another_is_a_mismatch() -> None:
    same = judge(status=201, stored_hash="h", age_seconds=5, new_hash="h")
    other = judge(status=201, stored_hash="h", age_seconds=5, new_hash="x")
    assert (same, other) == (Verdict.REPLAY, Verdict.MISMATCH)


def test_F7_unit_a_row_in_flight_is_in_progress_until_60_seconds() -> None:
    at_limit = judge(
        status=None, stored_hash="h", age_seconds=ABANDONED_AFTER_SECONDS, new_hash="h"
    )
    past = judge(
        status=None, stored_hash="h", age_seconds=ABANDONED_AFTER_SECONDS + 0.1, new_hash="h"
    )
    assert at_limit is Verdict.IN_PROGRESS
    assert past is Verdict.TAKE_OVER


def test_F7_unit_a_row_in_flight_for_another_request_is_a_mismatch_before_60_seconds() -> None:
    assert judge(status=None, stored_hash="h", age_seconds=1, new_hash="x") is Verdict.MISMATCH
    # An abandoned row is taken over whatever the new request is.
    assert judge(status=None, stored_hash="h", age_seconds=61, new_hash="x") is Verdict.TAKE_OVER


def test_F7_unit_a_row_older_than_24_hours_is_taken_over_in_any_state() -> None:
    at_limit = judge(status=201, stored_hash="h", age_seconds=EXPIRED_AFTER_SECONDS, new_hash="x")
    assert at_limit is Verdict.MISMATCH  # not yet expired: exactly 24 hours is still valid
    for status in (None, 201):
        past = judge(
            status=status, stored_hash="h", age_seconds=EXPIRED_AFTER_SECONDS + 1, new_hash="x"
        )
        assert past is Verdict.TAKE_OVER


def test_F7_unit_only_location_etag_and_content_type_are_stored() -> None:
    raw = [
        (b"content-type", b"application/json"),
        (b"Location", b"/v1/x/1"),
        (b"etag", b'"abc"'),
        (b"x-probe-extra", b"no"),
        (b"content-length", b"2"),
    ]
    assert stored_headers(raw) == {
        "content-type": "application/json",
        "location": "/v1/x/1",
        "etag": '"abc"',
    }


# -- Round 1 -------------------------------------------------------------------------------


def test_F7_unit_retry_after_is_a_stored_header() -> None:
    assert stored_headers([(b"retry-after", b"7"), (b"x-other", b"1")]) == {"retry-after": "7"}


def test_F7_unit_a_body_with_no_content_type_is_hashed_as_json() -> None:
    # FastAPI parses a body with no content type as JSON, so field order and spacing do not matter.
    assert request_hash("POST", "/v1/a", "", b'{"a":1,"b":2}') == request_hash(
        "POST", "/v1/a", "", b'{ "b": 2, "a": 1 }'
    )
    # Not JSON after all: its bytes.
    assert request_hash("POST", "/v1/a", "", b"a  b") != request_hash("POST", "/v1/a", "", b"a b")
