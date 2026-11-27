"""The device ID, the access declaration, and the token check (EPIC-11 spec, section 2.2).

- `DeviceIdMiddleware`: `X-Device-Id` (a UUID) is required on every request except the health
  check (the path `/v1/health`, any method, so a wrong method gets its `405`) and a CORS
  preflight (`OPTIONS` with `Access-Control-Request-Method`, which never carries custom
  headers); otherwise `400 request.invalid`. It runs before the access check. The contract
  declares the header, the `400`, the bearer scheme and the `401` (`install_access_contract`).
- `public` and `signed_in` are the FastAPI dependencies a route declares its access with
  (a capability, EPIC-02, will be a third kind: it registers with `declares_access`).
  `undeclared_routes` lists the routes with none; the test
  `test_F5_every_route_declares_its_access` fails CI for it. At run time an undeclared route
  is served as it is.
- `signed_in` checks the EdDSA access token (`Authorization: Bearer`, header `alg` `EdDSA`,
  claims `sub`, `sid`, `iat`, `exp`, no leeway) against the keys of the setting
  `ACCESS_TOKEN_PUBLIC_KEYS` (any one may verify), then asks the session hook that `identity`
  registers (`registry.set_session_hook`) whether the session is active, and returns a `Caller`.
  Failures are `401 auth.not_signed_in` or `401 auth.token_expired`. The keys are parsed once,
  at start-up (`install_access_keys`).
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
DEVICE_ID_PARAMETER = "DeviceId"
BEARER_SCHEME = "bearerAuth"
HEALTH_PATH = "/v1/health"
ALGORITHM = "EdDSA"
REQUIRED_CLAIMS = ["sub", "sid", "iat", "exp"]

# Private to the platform: only `signed_in` sets it, so no other code can plant a caller.
_CALLER_KEY = "_tripmate_platform_caller"
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


def _is_exempt(scope: Scope) -> bool:
    """The health check (every method) and a CORS preflight need no device ID."""
    if scope["path"] == HEALTH_PATH:
        return True
    return scope["method"] == "OPTIONS" and any(
        name == b"access-control-request-method" for name, _ in scope["headers"]
    )


class DeviceIdMiddleware:
    """Answers `400 request.invalid` to a request with no valid `X-Device-Id`.

    Pure ASGI. It raises `AppError`, which `RequestIdMiddleware` (the outermost one) answers,
    so `create_app` installs it before that one.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and not _is_exempt(scope):
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


def parse_public_keys(pems: tuple[str, ...]) -> tuple[Ed25519PublicKey, ...]:
    """The Ed25519 keys of the PEM texts. A text that is not one raises, naming no value."""
    keys = []
    for pem in pems:
        key = load_pem_public_key(pem.encode())
        if not isinstance(key, Ed25519PublicKey):
            raise TypeError("ACCESS_TOKEN_PUBLIC_KEYS holds a public key that is not Ed25519")
        keys.append(key)
    return tuple(keys)


def install_access_keys(app: FastAPI, pems: tuple[str, ...]) -> None:
    """Parse the keys once, at start-up; `signed_in` reads them from `app.state`."""
    app.state.access_keys = parse_public_keys(pems)


def _bearer_token(request: Request) -> str:
    header = request.headers.get("authorization")
    if not header:
        raise AppError(401, NOT_SIGNED_IN)
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise AppError(401, NOT_SIGNED_IN)
    return token.strip()


def _claims(token: str, keys: tuple[Ed25519PublicKey, ...]) -> dict[str, Any]:
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
    cached = request.scope.get("state", {}).get(_CALLER_KEY)
    if isinstance(cached, Caller):  # the general ceiling already checked this request
        return cached
    token = _bearer_token(request)
    claims = _claims(token, request.app.state.access_keys)
    try:
        caller = Caller(uuid.UUID(str(claims["sub"])), uuid.UUID(str(claims["sid"])))
    except ValueError:
        raise AppError(401, NOT_SIGNED_IN) from None
    hook = request.app.state.registry.session_hook
    if hook is None:  # nothing can say the session is active
        raise AppError(401, NOT_SIGNED_IN)
    if not await hook(caller.session_id):
        raise AppError(401, TOKEN_EXPIRED)
    request.scope.setdefault("state", {})[_CALLER_KEY] = caller
    return caller


# The declarations that need a signed-in caller: their routes carry `security` and a `401`.
_SIGNED_IN_DEPENDENCIES: set[Callable[..., Any]] = {signed_in}


def _uses(dependant: Dependant, declarations: set[Callable[..., Any]]) -> bool:
    return any(
        dependency.call in declarations or _uses(dependency, declarations)
        for dependency in dependant.dependencies
    )


def route_needs_signed_in(request: Request) -> bool:
    """Whether the route being served declares `signed_in` (so it is not `public`)."""
    route = request.scope.get("route")
    return isinstance(route, APIRoute) and _uses(route.dependant, _SIGNED_IN_DEPENDENCIES)


def _declares(dependant: Dependant) -> bool:
    return _uses(dependant, _ACCESS_DEPENDENCIES)


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


# -- The contract --------------------------------------------------------------------------


def _signed_in_operations(app: FastAPI) -> set[tuple[str, str]]:
    """`(method, path)` of each route that declares `signed_in` (path as in the contract)."""
    return {
        (method.lower(), context.path)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        and context.path is not None
        and _uses(context.original_route.dependant, _SIGNED_IN_DEPENDENCIES)
        for method in context.methods or ()
    }


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
    }


def install_access_contract(app: FastAPI) -> None:
    """Declare in the contract what the access check does at run time.

    Every operation but the health check takes the required header `X-Device-Id` (a UUID) and
    may answer `400`; a `signed_in` operation carries `security` (`bearerAuth`) and may answer
    `401`. The middleware stays the enforcer (it also covers paths the contract does not list).
    Install it after `install_openapi`, whose `ErrorResponse` it refers to.
    """
    documented = app.openapi

    def openapi() -> dict[str, Any]:
        schema = documented()
        components = schema["components"]  # `install_openapi` adds `ErrorResponse` there
        if DEVICE_ID_PARAMETER in components.setdefault("parameters", {}):
            return schema  # already done: the schema is cached and changed in place
        components["parameters"][DEVICE_ID_PARAMETER] = {
            "name": "X-Device-Id",
            "in": "header",
            "required": True,
            "description": "The ID of the device, a UUID the client makes once (security.md).",
            "schema": {"type": "string", "format": "uuid"},
        }
        components.setdefault("securitySchemes", {})[BEARER_SCHEME] = {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
        }
        signed_in_operations = _signed_in_operations(app)
        for path, item in schema["paths"].items():
            if path == HEALTH_PATH:
                continue
            for method, operation in item.items():
                operation.setdefault("parameters", []).insert(
                    0, {"$ref": f"#/components/parameters/{DEVICE_ID_PARAMETER}"}
                )
                responses = operation["responses"]
                responses.setdefault("400", _error_response("The request is not valid"))
                if (method, path) in signed_in_operations:
                    operation["security"] = [{BEARER_SCHEME: []}]
                    responses.setdefault("401", _error_response("Not signed in"))
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
