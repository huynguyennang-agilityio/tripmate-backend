"""F10 Log lines: cases F10-1 and F10-2 (epic-11 spec, version 3.3, section 2.6).

Every request writes one JSON line (`"event": "request"`) on standard output, captured with
`capfd`. PostgreSQL and Redis are on closed local ports and the general ceiling is off, so no
route here needs either (tests/INTERFACE.md, section "F10: Observability").
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.support import build_app, client_for, made_up_password

WHOAMI_PATH = "/v1/platform/whoami"

# A module that says every session is active, and serves a `public` route, a `public`
# sign-in route that reads a JSON body, and a `public` route with a path parameter `trip_id`.
HOOK_SOURCE = """\
async def session_is_active(session_id):
    return True


def register(registry):
    registry.set_session_hook(session_is_active)
"""

API_SOURCE = """\
from typing import Any

from fastapi import APIRouter, Body, Depends

from app.platform.access import public

router = APIRouter()


@router.get("/{segment}/open", dependencies=[Depends(public)])
async def open_route() -> dict[str, str]:
    return {{"open": "yes"}}


@router.post("/{segment}/sign-in", dependencies=[Depends(public)])
async def sign_in(body: dict[str, Any] = Body(...)) -> dict[str, bool]:
    return {{"ok": True}}


@router.get("/{segment}/trips/{{trip_id}}", dependencies=[Depends(public)])
async def trip_route(trip_id: str) -> dict[str, str]:
    return {{"trip": "yes"}}
"""


def _request_lines(output: str) -> list[dict[str, Any]]:
    """The JSON objects of the output whose `event` is `request`."""
    lines: list[dict[str, Any]] = []
    for raw in output.splitlines():
        raw = raw.strip()
        if not raw.startswith("{"):
            continue
        try:
            value = json.loads(raw)
        except ValueError:
            continue
        if isinstance(value, dict) and value.get("event") == "request":
            lines.append(value)
    return lines


def _lines_of_request(output: str, request_id: str) -> list[dict[str, Any]]:
    return [line for line in _request_lines(output) if line.get("request_id") == request_id]


def _token(key: Ed25519PrivateKey, user_id: uuid.UUID) -> str:
    now = int(time.time())
    claims = {"sub": str(user_id), "sid": str(uuid.uuid4()), "iat": now, "exp": now + 900}
    return jwt.encode(claims, key, algorithm="EdDSA")


@pytest.mark.asyncio
async def test_F10_1_each_request_writes_one_json_line_with_request_id_and_signed_in_user_id(
    set_settings, monkeypatch, module_folder, capfd
) -> None:
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("ACCESS_TOKEN_PUBLIC_KEYS", pem.decode())
    segment = f"f10seg{uuid.uuid4().hex[:8]}"
    module_folder({"__init__.py": HOOK_SOURCE, "api.py": API_SOURCE.format(segment=segment)})
    user_id = uuid.uuid4()
    app = build_app()
    capfd.readouterr()  # only what the requests write

    async with client_for(app) as client:
        signed_in = await client.get(
            WHOAMI_PATH,
            headers={
                "X-Device-Id": str(uuid.uuid4()),
                "Authorization": f"Bearer {_token(key, user_id)}",
            },
        )
        anonymous = await client.get(
            f"/v1/{segment}/open", headers={"X-Device-Id": str(uuid.uuid4())}
        )

    output = capfd.readouterr().out
    assert signed_in.status_code == 200, signed_in.text
    assert anonymous.status_code == 200, anonymous.text

    signed_in_lines = _lines_of_request(output, signed_in.headers["X-Request-Id"])
    assert len(signed_in_lines) == 1, f"expected one request line, got:\n{output}"
    line = signed_in_lines[0]
    assert line.get("user_id") == str(user_id), line
    assert line.get("path") == WHOAMI_PATH, line
    assert line.get("status") == 200, line

    anonymous_lines = _lines_of_request(output, anonymous.headers["X-Request-Id"])
    assert len(anonymous_lines) == 1, f"expected one request line, got:\n{output}"
    line = anonymous_lines[0]
    assert line.get("user_id") is None, line
    assert line.get("path") == f"/v1/{segment}/open", line
    assert line.get("status") == 200, line


@pytest.mark.asyncio
async def test_F10_1_request_line_carries_the_trip_id_of_a_route_with_a_trip_id(
    set_settings, module_folder, capfd
) -> None:
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    segment = f"f10seg{uuid.uuid4().hex[:8]}"
    module_folder({"__init__.py": HOOK_SOURCE, "api.py": API_SOURCE.format(segment=segment)})
    trip_id = str(uuid.uuid4())
    app = build_app()
    capfd.readouterr()

    async with client_for(app) as client:
        with_trip = await client.get(
            f"/v1/{segment}/trips/{trip_id}", headers={"X-Device-Id": str(uuid.uuid4())}
        )
        without_trip = await client.get(
            f"/v1/{segment}/open", headers={"X-Device-Id": str(uuid.uuid4())}
        )

    output = capfd.readouterr().out
    assert with_trip.status_code == 200, with_trip.text
    assert without_trip.status_code == 200, without_trip.text

    lines = _lines_of_request(output, with_trip.headers["X-Request-Id"])
    assert len(lines) == 1, f"expected one request line, got:\n{output}"
    assert lines[0].get("trip_id") == trip_id, lines[0]

    lines = _lines_of_request(output, without_trip.headers["X-Request-Id"])
    assert len(lines) == 1, f"expected one request line, got:\n{output}"
    assert lines[0].get("trip_id") is None, lines[0]


@pytest.mark.asyncio
async def test_F10_2_sign_in_request_log_line_holds_no_email_password_or_token(
    set_settings, module_folder, capfd
) -> None:
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    segment = f"f10seg{uuid.uuid4().hex[:8]}"
    module_folder({"__init__.py": HOOK_SOURCE, "api.py": API_SOURCE.format(segment=segment)})
    email = f"f10-{uuid.uuid4().hex[:12]}@example.com"
    password = made_up_password()
    token = f"tok{uuid.uuid4().hex}{uuid.uuid4().hex}"
    app = build_app()
    capfd.readouterr()

    async with client_for(app) as client:
        response = await client.post(
            f"/v1/{segment}/sign-in",
            json={"email": email, "password": password},
            headers={"X-Device-Id": str(uuid.uuid4()), "Authorization": f"Bearer {token}"},
        )

    captured = capfd.readouterr()
    output = captured.out + captured.err
    assert response.status_code == 200, response.text
    lines = _lines_of_request(captured.out, response.headers["X-Request-Id"])
    assert len(lines) == 1, f"expected one request line, got:\n{captured.out}"
    assert lines[0].get("path") == f"/v1/{segment}/sign-in", lines[0]
    for name, value in (("email", email), ("password", password), ("token", token)):
        assert value not in output, f"the {name} of the sign-in request was logged"
