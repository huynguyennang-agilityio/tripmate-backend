"""F5 Access declaration and token check: cases F5-1 to F5-10 (epic-11 spec, section 2.2).

A temporary module under app/ (as in F1-7) serves a `public` route and a `signed_in` route,
and registers a session hook in place of `identity`'s. The tests make their own Ed25519 key
pair for each test and give the app the public key; no key is kept in the repository. The
interface is in tests/INTERFACE.md, section "F5: Access".
"""

from __future__ import annotations

import base64
import importlib
import json
import math
import os
import subprocess
import sys
import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.support import (
    APP_DIR,
    REPO_ROOT,
    build_app,
    client_for,
    real_database_url,
    real_redis_url,
)

MODULE_PREFIX = "/v1"
DEVICE_HEADER = "X-Device-Id"
PUBLIC_KEYS_SETTING = "ACCESS_TOKEN_PUBLIC_KEYS"
ERROR_KEYS = {"code", "params", "fields", "requestId"}
DECLARATION_TEST = (
    "tests/api/platform/test_F5_access_declaration.py::test_F5_every_route_declares_its_access"
)

INIT_SOURCE = """\
import uuid

# Session IDs (as strings) the hook reports as ended, and every session ID it was asked about.
ENDED: set[str] = set()
ASKED: list[str] = []


async def session_is_active(session_id: uuid.UUID) -> bool:
    ASKED.append(str(session_id))
    return str(session_id) not in ENDED


def register(registry):
    registry.set_session_hook(session_is_active)
"""

API_SOURCE = """\
from typing import Annotated

from fastapi import APIRouter, Depends

from app.platform.access import Caller, public, signed_in

router = APIRouter()


@router.get("/{segment}/public", dependencies=[Depends(public)])
async def open_route() -> dict[str, str]:
    return {{"ok": "public"}}


@router.get("/{segment}/me")
async def me(caller: Annotated[Caller, Depends(signed_in)]) -> dict[str, str]:
    return {{"userId": str(caller.user_id), "sessionId": str(caller.session_id)}}
"""

UNDECLARED_SOURCE = """\


@router.get("/{segment}/undeclared")
async def undeclared() -> dict[str, str]:
    return {{"ok": "undeclared"}}
"""


# -- Keys and tokens -----------------------------------------------------------------------


def _new_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def _public_pem(private_key: Ed25519PrivateKey) -> str:
    return (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def _claims(user_id: str, session_id: str, expires_at: int) -> dict[str, Any]:
    return {"sub": user_id, "sid": session_id, "iat": int(time.time()), "exp": expires_at}


def _token(
    private_key: Ed25519PrivateKey,
    *,
    user_id: str | None = None,
    session_id: str | None = None,
    expires_at: int | None = None,
) -> str:
    """An EdDSA access token, valid for 15 minutes unless `expires_at` is given."""
    claims = _claims(
        user_id or str(uuid.uuid4()),
        session_id or str(uuid.uuid4()),
        expires_at if expires_at is not None else int(time.time()) + 15 * 60,
    )
    return jwt.encode(claims, private_key, algorithm="EdDSA")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unsigned_token() -> str:
    """A token with the algorithm `none` and no signature, otherwise valid."""
    header = _b64url(json.dumps({"alg": "none", "typ": "JWT"}).encode())
    claims = _claims(str(uuid.uuid4()), str(uuid.uuid4()), int(time.time()) + 15 * 60)
    return f"{header}.{_b64url(json.dumps(claims).encode())}."


# -- Requests ------------------------------------------------------------------------------


def _headers(token: str | None = None, *, device: str | None = "valid") -> dict[str, str]:
    headers: dict[str, str] = {}
    if device == "valid":
        headers[DEVICE_HEADER] = str(uuid.uuid4())
    elif device is not None:
        headers[DEVICE_HEADER] = device
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _error_code(response: Any) -> str:
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == ERROR_KEYS, error
    return str(error["code"])


def _assert_error(response: Any, status: int, code: str) -> None:
    assert response.status_code == status, f"{response.status_code} {response.text}"
    assert _error_code(response) == code, response.text


class _Probe:
    """The temporary module: its route paths and its session hook's state."""

    def __init__(self, name: str) -> None:
        segment = name.replace("_", "-")
        self.name = name
        self.public = f"{MODULE_PREFIX}/{segment}/public"
        self.me = f"{MODULE_PREFIX}/{segment}/me"
        self.undeclared = f"{MODULE_PREFIX}/{segment}/undeclared"

    @property
    def module(self) -> Any:
        return importlib.import_module(f"app.{self.name}")

    def end_session(self, session_id: str) -> None:
        self.module.ENDED.add(session_id)

    def asked(self) -> list[str]:
        return list(self.module.ASKED)


def _write_probe(module_folder, *, undeclared: bool = False) -> _Probe:
    name = module_folder({"__init__.py": INIT_SOURCE})
    segment = name.replace("_", "-")
    source = API_SOURCE.format(segment=segment)
    if undeclared:
        source += UNDECLARED_SOURCE.format(segment=segment)
    (APP_DIR / name / "api.py").write_text(source, encoding="utf-8")
    return _Probe(name)


@pytest.fixture
def signing_key(set_settings, monkeypatch: pytest.MonkeyPatch) -> Ed25519PrivateKey:
    """Settings on closed ports, plus a fresh test key pair whose public key the app trusts."""
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    private_key = _new_private_key()
    monkeypatch.setenv(PUBLIC_KEYS_SETTING, _public_pem(private_key))
    return private_key


# -- F5-1 ----------------------------------------------------------------------------------


def _run_declaration_test() -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", DECLARATION_TEST, "-q", "-p", "no:cacheprovider"],
        cwd=REPO_ROOT,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_F5_1_route_with_no_access_declaration_fails_the_declaration_test_and_names_it(
    signing_key, module_folder
) -> None:
    probe = _write_probe(module_folder)
    declared_only = _run_declaration_test()
    assert declared_only.returncode == 0, (
        "the declaration test must pass when every route declares its access:\n"
        f"{declared_only.stdout}{declared_only.stderr}"
    )

    (APP_DIR / probe.name / "api.py").write_text(
        (APP_DIR / probe.name / "api.py").read_text(encoding="utf-8")
        + UNDECLARED_SOURCE.format(segment=probe.name.replace("_", "-")),
        encoding="utf-8",
    )
    with_undeclared = _run_declaration_test()
    output = with_undeclared.stdout + with_undeclared.stderr

    assert with_undeclared.returncode == 1, f"the declaration test did not fail:\n{output}"
    assert probe.undeclared in output, f"the failure does not name {probe.undeclared}:\n{output}"
    assert probe.public not in output and probe.me not in output, (
        f"the failure names a route that declares its access:\n{output}"
    )


# -- F5-2 to F5-8 --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F5_2_signed_in_route_with_valid_token_and_active_session_knows_user_and_session(
    signing_key, module_folder
) -> None:
    probe = _write_probe(module_folder)
    user_id, session_id = str(uuid.uuid4()), str(uuid.uuid4())
    token = _token(signing_key, user_id=user_id, session_id=session_id)

    async with client_for(build_app()) as client:
        response = await client.get(probe.me, headers=_headers(token))

    assert response.status_code == 200, response.text
    assert response.json() == {"userId": user_id, "sessionId": session_id}
    assert session_id in probe.asked(), "identity's session hook was not asked"


@pytest.mark.asyncio
async def test_F5_3_public_route_with_no_token_returns_200(signing_key, module_folder) -> None:
    probe = _write_probe(module_folder)

    async with client_for(build_app()) as client:
        response = await client.get(probe.public, headers=_headers())

    assert response.status_code == 200, response.text
    assert response.json() == {"ok": "public"}


@pytest.mark.asyncio
async def test_F5_4_signed_in_route_with_no_authorization_header_is_401_not_signed_in(
    signing_key, module_folder
) -> None:
    probe = _write_probe(module_folder)

    async with client_for(build_app()) as client:
        response = await client.get(probe.me, headers=_headers())

    _assert_error(response, 401, "auth.not_signed_in")


@pytest.mark.asyncio
async def test_F5_5_token_past_its_expiry_is_401_token_expired(signing_key, module_folder) -> None:
    probe = _write_probe(module_folder)
    token = _token(signing_key, expires_at=int(time.time()) - 60)

    async with client_for(build_app()) as client:
        response = await client.get(probe.me, headers=_headers(token))

    _assert_error(response, 401, "auth.token_expired")


@pytest.mark.asyncio
async def test_F5_6_token_signed_with_another_key_or_algorithm_none_is_401_not_signed_in(
    signing_key, module_folder
) -> None:
    probe = _write_probe(module_folder)
    other_key_token = _token(_new_private_key())
    unsigned_token = _unsigned_token()

    async with client_for(build_app()) as client:
        other_key = await client.get(probe.me, headers=_headers(other_key_token))
        algorithm_none = await client.get(probe.me, headers=_headers(unsigned_token))

    _assert_error(other_key, 401, "auth.not_signed_in")
    _assert_error(algorithm_none, 401, "auth.not_signed_in")


@pytest.mark.asyncio
async def test_F5_7_valid_token_whose_session_the_hook_reports_ended_is_401_token_expired(
    signing_key, module_folder
) -> None:
    probe = _write_probe(module_folder)
    session_id = str(uuid.uuid4())
    token = _token(signing_key, session_id=session_id)

    app = build_app()
    probe.end_session(session_id)
    async with client_for(app) as client:
        response = await client.get(probe.me, headers=_headers(token))

    _assert_error(response, 401, "auth.token_expired")
    assert session_id in probe.asked(), "identity's session hook was not asked"


@pytest.mark.asyncio
async def test_F5_8_token_one_second_before_expiry_passes_and_one_second_after_is_token_expired(
    signing_key, module_folder
) -> None:
    probe = _write_probe(module_folder)

    async with client_for(build_app()) as client:
        not_yet = _token(signing_key, expires_at=math.ceil(time.time()) + 1)
        before = await client.get(probe.me, headers=_headers(not_yet))
        just_past = _token(signing_key, expires_at=math.floor(time.time()) - 1)
        after = await client.get(probe.me, headers=_headers(just_past))

    assert before.status_code == 200, before.text
    _assert_error(after, 401, "auth.token_expired")


# -- F5-9 and F5-10 ------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F5_9_missing_or_malformed_device_id_is_400_request_invalid(
    signing_key, module_folder
) -> None:
    probe = _write_probe(module_folder)
    token = _token(signing_key)

    async with client_for(build_app()) as client:
        responses = {
            "public, no device ID": await client.get(probe.public, headers=_headers(device=None)),
            "public, not a UUID": await client.get(
                probe.public, headers=_headers(device="not-a-uuid")
            ),
            "signed in, no device ID": await client.get(
                probe.me, headers=_headers(token, device=None)
            ),
            "signed in, not a UUID": await client.get(
                probe.me, headers=_headers(token, device="not-a-uuid")
            ),
        }

    for given, response in responses.items():
        assert response.status_code == 400, f"{given}: {response.status_code} {response.text}"
        assert _error_code(response) == "request.invalid", f"{given}: {response.text}"


@pytest.mark.asyncio
async def test_F5_10_health_check_with_no_device_id_returns_200(
    signing_key, module_folder, monkeypatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", real_database_url())
    monkeypatch.setenv("REDIS_URL", real_redis_url())
    probe = _write_probe(module_folder)

    async with client_for(build_app()) as client:
        health = await client.get(f"{MODULE_PREFIX}/health")
        # The same app asks for the device ID on any other route (F5-9): the health check
        # is the one exception.
        other_route = await client.get(probe.public, headers=_headers(device=None))

    assert health.status_code == 200, health.text
    _assert_error(other_route, 400, "request.invalid")
