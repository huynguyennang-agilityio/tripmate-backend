"""Helpers of the F6 rate limit tests (tests/INTERFACE.md, section "F6: Rate limits").

A temporary module under app/ (the `module_folder` fixture, as in F5) serves three routes:
a `public` route, a `signed_in` route, and a `public` route with its own limit, keyed by the
header `X-Probe-Key`. Each handler records that it ran. Every caller gets its own made-up IP
address through `X-Forwarded-For`, and every route key starts with the module's unique name,
so no two tests (and no other card's tests) share a Redis counter.
"""

from __future__ import annotations

import importlib
import math
import secrets
import time
import uuid
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.support import APP_DIR

MODULE_PREFIX = "/v1"
DEVICE_HEADER = "X-Device-Id"
FORWARDED_HEADER = "X-Forwarded-For"
PROBE_KEY_HEADER = "X-Probe-Key"
CEILING_SETTING = "RATE_LIMIT_PER_MINUTE"
PUBLIC_KEYS_SETTING = "ACCESS_TOKEN_PUBLIC_KEYS"
CEILING_WINDOW_SECONDS = 60
ERROR_KEYS = {"code", "params", "fields", "requestId"}

INIT_SOURCE = """\
import uuid

# The name of each handler that ran, in order.
CALLS: list[str] = []


async def session_is_active(session_id: uuid.UUID) -> bool:
    return True


def register(registry):
    registry.set_session_hook(session_is_active)
"""

API_SOURCE = """\
from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.platform.access import Caller, public, signed_in
from app.platform.rate_limits import rate_limit

from . import CALLS

router = APIRouter()


def probe_key(request: Request) -> str:
    return "{name}:" + request.headers.get("{key_header}", "none")


@router.get("/{segment}/open", dependencies=[Depends(public)])
async def open_route() -> dict[str, str]:
    CALLS.append("open")
    return {{"ok": "open"}}


@router.get("/{segment}/me")
async def me(caller: Annotated[Caller, Depends(signed_in)]) -> dict[str, str]:
    CALLS.append("me")
    return {{"userId": str(caller.user_id)}}


@router.get(
    "/{segment}/limited",
    dependencies=[
        Depends(public),
        Depends(rate_limit(limit={limit}, window_seconds={window}, key=probe_key)),
    ],
)
async def limited() -> dict[str, str]:
    CALLS.append("limited")
    return {{"ok": "limited"}}
"""


class Probe:
    """The temporary module: its route paths and the handlers that ran."""

    def __init__(self, name: str) -> None:
        segment = name.replace("_", "-")
        self.name = name
        self.open = f"{MODULE_PREFIX}/{segment}/open"
        self.me = f"{MODULE_PREFIX}/{segment}/me"
        self.limited = f"{MODULE_PREFIX}/{segment}/limited"

    def calls(self) -> list[str]:
        return list(importlib.import_module(f"app.{self.name}").CALLS)


def write_probe(module_folder: Any, *, route_limit: int = 2, route_window: int = 10) -> Probe:
    """Create the probe module; its `limited` route allows `route_limit` per `route_window` s."""
    name = module_folder({"__init__.py": INIT_SOURCE})
    source = API_SOURCE.format(
        name=name,
        segment=name.replace("_", "-"),
        key_header=PROBE_KEY_HEADER,
        limit=route_limit,
        window=route_window,
    )
    (APP_DIR / name / "api.py").write_text(source, encoding="utf-8")
    return Probe(name)


# -- Callers -------------------------------------------------------------------------------


def new_ip() -> str:
    """A made-up IP address no other test uses (from the 10.0.0.0/8 private range)."""
    return "10." + ".".join(str(secrets.randbelow(256)) for _ in range(3))


def headers(
    ip: str,
    *,
    token: str | None = None,
    probe_key: str | None = None,
    forwarded_before: str | None = None,
) -> dict[str, str]:
    """Headers of one request: a valid device ID, and `ip` as the last X-Forwarded-For entry."""
    forwarded = ip if forwarded_before is None else f"{forwarded_before}, {ip}"
    result = {DEVICE_HEADER: str(uuid.uuid4()), FORWARDED_HEADER: forwarded}
    if token is not None:
        result["Authorization"] = f"Bearer {token}"
    if probe_key is not None:
        result[PROBE_KEY_HEADER] = probe_key
    return result


# -- Keys and tokens (as in F5) ------------------------------------------------------------


def new_private_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def public_pem(private_key: Ed25519PrivateKey) -> str:
    return (
        private_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("ascii")
    )


def token_for(private_key: Ed25519PrivateKey, user_id: str) -> str:
    """An EdDSA access token for `user_id`, valid for 15 minutes."""
    now = int(time.time())
    claims = {"sub": user_id, "sid": str(uuid.uuid4()), "iat": now, "exp": now + 15 * 60}
    return jwt.encode(claims, private_key, algorithm="EdDSA")


# -- Answers -------------------------------------------------------------------------------


def error_code(response: Any) -> str:
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == ERROR_KEYS, error
    return str(error["code"])


def assert_passes(response: Any, what: str) -> None:
    assert response.status_code == 200, f"{what}: {response.status_code} {response.text}"


def assert_limited(response: Any, what: str) -> int:
    """`429 rate.limited` with a whole-seconds `Retry-After`; returns its value."""
    assert response.status_code == 429, f"{what}: {response.status_code} {response.text}"
    assert error_code(response) == "rate.limited", f"{what}: {response.text}"
    retry_after = response.headers.get("Retry-After")
    assert retry_after is not None, f"{what}: no Retry-After header"
    assert retry_after.isdigit(), f"{what}: Retry-After {retry_after!r} is not whole seconds"
    return int(retry_after)


def seconds_left_bounds(
    window: int, first_sent: float, first_answered: float, over_sent: float, over_answered: float
) -> tuple[int, int]:
    """The lowest and highest whole seconds left (rounded up) the over-limit answer can give.

    The window starts while the first request is served and lasts `window` seconds; the
    over-limit answer is made while the request over the limit is served.
    """
    lowest = math.ceil(first_sent + window - over_answered)
    highest = math.ceil(first_answered + window - over_sent)
    return lowest, highest
