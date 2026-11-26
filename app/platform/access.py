"""The device ID, the access declaration, and the token check (EPIC-11 spec, section 2.2).

- `DeviceIdMiddleware`: `X-Device-Id` (a UUID) is required on every request except the health
  check `GET /v1/health`; otherwise `400 request.invalid`. It runs before the access check.
- `public` and `signed_in` are the FastAPI dependencies a route declares its access with
  (a capability, EPIC-02, will be a third kind: it registers with `declares_access`).
  `undeclared_routes` lists the routes with none; the test
  `test_F5_every_route_declares_its_access` fails CI for it. At run time an undeclared route
  is served as it is.
- `signed_in` checks the EdDSA access token (`Authorization: Bearer`, header `alg` `EdDSA`,
  claims `sub`, `sid`, `iat`, `exp`, no leeway) against the keys of the setting
  `ACCESS_TOKEN_PUBLIC_KEYS` (any one may verify), then asks the session hook that `identity`
  registers (`registry.set_session_hook`) whether the session is active, and returns a `Caller`.
  Failures are `401 auth.not_signed_in` or `401 auth.token_expired`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import jwt
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from fastapi import FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute, iter_route_contexts
from starlette.types import ASGIApp, Receive, Scope, Send

from app.platform.errors import (
    FIELD_INVALID,
    FIELD_MISSING,
    NOT_SIGNED_IN,
    REQUEST_INVALID,
    TOKEN_EXPIRED,
    AppError,
)

DEVICE_ID_HEADER = "x-device-id"
HEALTH_PATH = "/v1/health"
ALGORITHM = "EdDSA"
REQUIRED_CLAIMS = ["sub", "sid", "iat", "exp"]

_ACCESS_DEPENDENCIES: set[Callable[..., Any]] = set()


def declares_access[F: Callable[..., Any]](dependency: F) -> F:
    """Mark a dependency as an access declaration (`public`, `signed_in`, a capability)."""
    _ACCESS_DEPENDENCIES.add(dependency)
    return dependency


@dataclass(frozen=True)
class Caller:
    """Who calls: the user ID and session ID of the access token."""

    user_id: uuid.UUID
    session_id: uuid.UUID


# -- The device ID -------------------------------------------------------------------------


def _is_device_id(value: str) -> bool:
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


class DeviceIdMiddleware:
    """Answers `400 request.invalid` to a request with no valid `X-Device-Id`.

    Pure ASGI. It raises `AppError`, which `RequestIdMiddleware` (the outermost one) answers,
    so `create_app` installs it before that one.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not (
            scope["method"] == "GET" and scope["path"] == HEALTH_PATH
        ):
            headers = dict(scope["headers"])
            value = headers.get(DEVICE_ID_HEADER.encode(), b"").decode("latin-1")
            if not _is_device_id(value):
                code = FIELD_INVALID if value else FIELD_MISSING
                raise AppError(
                    400, REQUEST_INVALID, fields=[{"field": "X-Device-Id", "code": code}]
                )
        await self.app(scope, receive, send)


def install_device_id(app: FastAPI) -> None:
    app.add_middleware(DeviceIdMiddleware)


# -- The declaration -----------------------------------------------------------------------


@declares_access
async def public() -> None:
    """Declares a route open to everyone: nothing is checked."""


def _public_keys(request: Request) -> list[Ed25519PublicKey]:
    keys = []
    for pem in request.app.state.settings.access_token_public_keys:
        key = load_pem_public_key(pem.encode())
        if isinstance(key, Ed25519PublicKey):  # always, the setting is checked at start-up
            keys.append(key)
    return keys


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        raise AppError(401, NOT_SIGNED_IN)
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AppError(401, NOT_SIGNED_IN)
    return token.strip()


def _claims(token: str, keys: list[Ed25519PublicKey]) -> dict[str, Any]:
    """The claims of a token that one key verifies and that has not expired, no leeway."""
    for key in keys:
        try:
            return jwt.decode(
                token,
                key,
                algorithms=[ALGORITHM],
                # PyJWT's default leeway is 0, and the spec allows none.
                options={"require": REQUIRED_CLAIMS},
            )
        except jwt.ExpiredSignatureError:  # only after the signature checked
            raise AppError(401, TOKEN_EXPIRED) from None
        except jwt.InvalidTokenError:
            continue  # another key may verify it
    raise AppError(401, NOT_SIGNED_IN)


@declares_access
async def signed_in(request: Request) -> Caller:
    """Declares a route for a signed-in user, checks the token and session, returns the caller."""
    token = _bearer_token(request)
    claims = _claims(token, _public_keys(request))
    try:
        caller = Caller(uuid.UUID(str(claims["sub"])), uuid.UUID(str(claims["sid"])))
    except ValueError:
        raise AppError(401, NOT_SIGNED_IN) from None
    hook = request.app.state.registry.session_hook
    if hook is None:  # nothing can say the session is active
        raise AppError(401, NOT_SIGNED_IN)
    if not await hook(caller.session_id):
        raise AppError(401, TOKEN_EXPIRED)
    return caller


def _declares(dependant: Dependant) -> bool:
    return any(
        dependency.call in _ACCESS_DEPENDENCIES or _declares(dependency)
        for dependency in dependant.dependencies
    )


def undeclared_routes(app: FastAPI) -> list[str]:
    """The routes of `app` with no access declaration, each as `METHOD /full/path`, sorted.

    A route of an included router is listed with its full path. FastAPI's own pages (OpenAPI,
    docs) are not `APIRoute`s and are not listed.
    """
    undeclared: list[str] = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if isinstance(route, APIRoute) and not _declares(route.dependant):
            undeclared.extend(f"{method} {context.path}" for method in context.methods or ())
    return sorted(undeclared)
