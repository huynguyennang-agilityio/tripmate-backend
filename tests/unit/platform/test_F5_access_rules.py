"""F5 `app/platform/access.py`: device ID, access declaration, token check (code agent's own tests).

These run on a small app of their own, with no PostgreSQL or Redis, so mutation testing (which
runs `tests/unit` only) reaches every rule of the file. The locked cases F5-1 to F5-10 are in
`tests/api/platform/`.
"""

from __future__ import annotations

import base64
import json
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import APIRouter, Depends, FastAPI
from starlette.applications import Starlette
from starlette.routing import Mount

from app.platform import access
from app.platform.access import (
    Caller,
    declares_access,
    install_access_contract,
    install_access_keys,
    install_device_id,
    public,
    signed_in,
    undeclared_routes,
)
from app.platform.config import load_settings
from app.platform.errors import install_error_handlers, install_openapi, install_request_id
from app.platform.modules import Registry

DEVICE = str(uuid.uuid4())
ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/d",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "RELEASE_VERSION": "x",
    "RATE_LIMIT_PER_MINUTE": "0",
}


def _pem(key: Ed25519PrivateKey) -> str:
    return (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )


def _app(
    *keys: Ed25519PrivateKey,
    hook: Callable[[uuid.UUID], Awaitable[bool]] | None = None,
    asked: list[uuid.UUID] | None = None,
) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    settings = {**ENV, "ACCESS_TOKEN_PUBLIC_KEYS": "".join(_pem(k) for k in keys)}
    app.state.settings = load_settings({k: v for k, v in settings.items() if v})
    registry = Registry()
    if hook is None:

        async def hook(session_id: uuid.UUID) -> bool:
            if asked is not None:
                asked.append(session_id)
            return True

    registry.set_session_hook(hook)
    app.state.registry = registry

    @app.get("/v1/health", dependencies=[Depends(public)])
    async def health() -> dict[str, str]:
        return {"ok": "health"}

    @app.post("/v1/health", dependencies=[Depends(public)])
    async def health_post() -> dict[str, str]:
        return {"ok": "post"}

    @app.get("/v1/open", dependencies=[Depends(public)])
    async def open_route() -> dict[str, str]:
        return {"ok": "open"}

    @app.get("/v1/me")
    async def me(caller: Annotated[Caller, Depends(signed_in)]) -> dict[str, str]:
        return {"userId": str(caller.user_id), "sessionId": str(caller.session_id)}

    install_openapi(app)
    install_access_contract(app)
    install_access_keys(app, app.state.settings.access_token_public_keys)
    install_device_id(app)
    install_request_id(app)
    return app


async def _get(app: FastAPI, path: str, headers: dict[str, str], method: str = "GET") -> Any:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        return await client.request(method, path, headers=headers)


def _token(key: Ed25519PrivateKey, **overrides: Any) -> str:
    now = int(time.time())
    claims: dict[str, Any] = {
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "iat": now,
        "exp": now + 900,
    }
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, key, algorithm="EdDSA")


def _auth(token: str, scheme: str = "Bearer") -> dict[str, str]:
    return {"X-Device-Id": DEVICE, "Authorization": f"{scheme} {token}"}


async def _code(app: FastAPI, headers: dict[str, str], path: str = "/v1/me") -> tuple[int, Any]:
    response = await _get(app, path, headers)
    body = response.json()
    return response.status_code, body.get("error", {}).get("code", body)


# -- Device ID -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "valid"),
    [
        (DEVICE, True),
        (DEVICE.upper(), True),
        (DEVICE.replace("-", ""), False),
        ("{" + DEVICE + "}", False),
        ("urn:uuid:" + DEVICE, False),
        (DEVICE + "0", False),
        ("not-a-uuid", False),
        ("", False),
    ],
)
def test_F5_device_id_is_a_hyphenated_uuid(value: str, valid: bool) -> None:
    assert access._is_device_id(value) is valid


@pytest.mark.asyncio
async def test_F5_device_id_missing_or_invalid_is_400_with_its_field_and_code() -> None:
    app = _app(Ed25519PrivateKey.generate())
    missing = await _get(app, "/v1/open", {})
    invalid = await _get(app, "/v1/open", {"X-Device-Id": "nope"})
    unknown = await _get(app, "/v1/nothing", {})
    ok = await _get(app, "/v1/open", {"X-Device-Id": DEVICE})

    assert missing.status_code == invalid.status_code == unknown.status_code == 400
    assert missing.json()["error"]["code"] == "request.invalid"
    assert missing.json()["error"]["fields"] == [
        {"field": "X-Device-Id", "code": "request.field_missing"}
    ]
    assert invalid.json()["error"]["fields"] == [
        {"field": "X-Device-Id", "code": "request.field_invalid"}
    ]
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_F5_the_health_path_is_exempt_from_the_device_id_for_every_method() -> None:
    app = _app(Ed25519PrivateKey.generate())
    assert (await _get(app, "/v1/health", {})).status_code == 200
    assert (await _get(app, "/v1/health", {}, "POST")).status_code == 200
    assert (await _get(app, "/v1/health", {"X-Device-Id": "bad"})).status_code == 200
    # A method the route does not serve gets the routing answer, not a 400 (R1-3).
    for method in ("TRACE", "DELETE", "PUT"):
        response = await _get(app, "/v1/health", {}, method)
        assert response.status_code == 405, method
        assert response.json()["error"]["code"] == "request.method_not_allowed"
    # Only that exact path: a longer one is another route.
    assert (await _get(app, "/v1/health/x", {})).status_code == 400
    assert (await _get(app, "/v1/healthz", {})).status_code == 400


@pytest.mark.asyncio
async def test_F5_a_cors_preflight_needs_no_device_id() -> None:
    app = _app(Ed25519PrivateKey.generate())
    preflight = {"Access-Control-Request-Method": "GET", "Origin": "https://app.example"}
    # With the preflight header the check is skipped: routing answers (no OPTIONS route: 405).
    assert (await _get(app, "/v1/me", preflight, "OPTIONS")).status_code == 405
    # An OPTIONS request without it is an ordinary request.
    plain = await _get(app, "/v1/me", {"Origin": "https://app.example"}, "OPTIONS")
    assert plain.status_code == 400
    # The header on another method does not skip the check.
    assert (await _get(app, "/v1/me", preflight, "GET")).status_code == 400


@pytest.mark.asyncio
async def test_F5_device_id_middleware_leaves_other_scopes_alone() -> None:
    seen: list[str] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        seen.append(scope["type"])

    await access.DeviceIdMiddleware(inner)({"type": "lifespan"}, None, None)  # type: ignore[arg-type]
    assert seen == ["lifespan"]


@pytest.mark.asyncio
async def test_F5_device_id_middleware_hands_the_same_receive_and_send_on() -> None:
    got: list[tuple[Any, Any]] = []

    async def inner(scope: Any, receive: Any, send: Any) -> None:
        got.append((receive, send))

    receive, send = object(), object()
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/v1/x",
        "headers": [(b"x-device-id", DEVICE.encode())],
    }
    await access.DeviceIdMiddleware(inner)(scope, receive, send)  # type: ignore[arg-type]
    assert got == [(receive, send)]


# -- Token check ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_F5_valid_token_gives_the_caller_and_asks_the_hook_about_its_session() -> None:
    key = Ed25519PrivateKey.generate()
    asked: list[uuid.UUID] = []
    user, session = uuid.uuid4(), uuid.uuid4()
    response = await _get(
        _app(key, asked=asked),
        "/v1/me",
        _auth(_token(key, sub=str(user), sid=str(session))),
    )
    assert response.status_code == 200
    assert response.json() == {"userId": str(user), "sessionId": str(session)}
    assert asked == [session]


@pytest.mark.asyncio
async def test_F5_authorization_header_forms() -> None:
    key = Ed25519PrivateKey.generate()
    app = _app(key)
    token = _token(key)
    assert (await _code(app, _auth(token, "bearer")))[0] == 200
    assert (await _code(app, _auth(token, "BEARER")))[0] == 200
    assert (await _code(app, _auth(token, "Basic"))) == (401, "auth.not_signed_in")
    assert (await _code(app, {"X-Device-Id": DEVICE})) == (401, "auth.not_signed_in")
    assert (await _code(app, {"X-Device-Id": DEVICE, "Authorization": "Bearer"})) == (
        401,
        "auth.not_signed_in",
    )
    assert (await _code(app, {"X-Device-Id": DEVICE, "Authorization": "Bearer  "})) == (
        401,
        "auth.not_signed_in",
    )
    assert (await _code(app, {"X-Device-Id": DEVICE, "Authorization": token})) == (
        401,
        "auth.not_signed_in",
    )
    assert (await _code(app, {"X-Device-Id": DEVICE, "Authorization": f"Bearer {token} "}))[
        0
    ] == 200


@pytest.mark.asyncio
async def test_F5_expiry_has_no_leeway_and_only_after_the_signature_checked() -> None:
    key, other = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    app = _app(key)
    now = int(time.time())
    assert await _code(app, _auth(_token(key, exp=now))) == (401, "auth.token_expired")
    assert await _code(app, _auth(_token(key, exp=now - 60))) == (401, "auth.token_expired")
    assert (await _code(app, _auth(_token(key, exp=now + 5))))[0] == 200
    # An expired token of another key tells nothing: it is not signed in.
    assert await _code(app, _auth(_token(other, exp=now - 60))) == (401, "auth.not_signed_in")


@pytest.mark.asyncio
async def test_F5_a_token_issued_in_the_future_is_not_accepted_and_there_is_no_leeway() -> None:
    key = Ed25519PrivateKey.generate()
    future = int(time.time()) + 1
    assert await _code(_app(key), _auth(_token(key, iat=future))) == (401, "auth.not_signed_in")


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["sub", "sid", "iat", "exp"])
async def test_F5_a_token_without_a_claim_is_not_signed_in(claim: str) -> None:
    key = Ed25519PrivateKey.generate()
    token = _token(key, **{claim: None})
    assert await _code(_app(key), _auth(token)) == (401, "auth.not_signed_in")


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", ["sub", "sid"])
async def test_F5_a_claim_that_is_not_a_uuid_is_not_signed_in(claim: str) -> None:
    key = Ed25519PrivateKey.generate()
    assert await _code(_app(key), _auth(_token(key, **{claim: "not-a-uuid"}))) == (
        401,
        "auth.not_signed_in",
    )


@pytest.mark.asyncio
async def test_F5_any_of_the_keys_verifies_and_another_key_does_not() -> None:
    first, second, stranger = (Ed25519PrivateKey.generate() for _ in range(3))
    app = _app(first, second)
    assert (await _code(app, _auth(_token(first))))[0] == 200
    assert (await _code(app, _auth(_token(second))))[0] == 200
    assert await _code(app, _auth(_token(stranger))) == (401, "auth.not_signed_in")
    # Expired and signed by the second key: expired, although the first key does not verify it.
    expired = _token(second, exp=int(time.time()) - 60)
    assert await _code(app, _auth(expired)) == (401, "auth.token_expired")


@pytest.mark.asyncio
async def test_F5_algorithm_none_and_a_symmetric_algorithm_are_not_signed_in() -> None:
    key = Ed25519PrivateKey.generate()
    app = _app(key)

    def b64(data: bytes) -> str:
        return base64.urlsafe_b64encode(data).rstrip(b"=").decode()

    now = int(time.time())
    claims = {"sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "iat": now, "exp": now + 900}
    unsigned = f"{b64(json.dumps({'alg': 'none'}).encode())}.{b64(json.dumps(claims).encode())}."
    hs256 = jwt.encode(claims, "x" * 32, algorithm="HS256")
    for token in (unsigned, hs256, "not.a.token", "garbage"):
        assert await _code(app, _auth(token)) == (401, "auth.not_signed_in")


@pytest.mark.asyncio
async def test_F5_no_keys_means_every_signed_in_route_is_401_not_signed_in() -> None:
    key = Ed25519PrivateKey.generate()
    assert await _code(_app(), _auth(_token(key))) == (401, "auth.not_signed_in")
    assert (await _get(_app(), "/v1/open", {"X-Device-Id": DEVICE})).status_code == 200


@pytest.mark.asyncio
async def test_F5_session_hook_paths() -> None:
    key = Ed25519PrivateKey.generate()
    ended = uuid.uuid4()

    async def hook(session_id: uuid.UUID) -> bool:
        return session_id != ended

    app = _app(key, hook=hook)
    assert await _code(app, _auth(_token(key, sid=str(ended)))) == (401, "auth.token_expired")
    assert (await _code(app, _auth(_token(key))))[0] == 200

    app.state.registry.session_hook = None  # no module registered one: nothing says it is active
    assert await _code(app, _auth(_token(key))) == (401, "auth.not_signed_in")


@pytest.mark.asyncio
async def test_F5_the_hook_is_not_asked_when_the_token_fails() -> None:
    key = Ed25519PrivateKey.generate()
    asked: list[uuid.UUID] = []
    app = _app(key, asked=asked)
    await _code(app, _auth(_token(Ed25519PrivateKey.generate())))
    await _code(app, _auth(_token(key, exp=int(time.time()) - 60)))
    assert asked == []


@pytest.mark.asyncio
async def test_F5_public_routes_read_no_token() -> None:
    response = await _get(
        _app(Ed25519PrivateKey.generate()),
        "/v1/open",
        {"X-Device-Id": DEVICE, "Authorization": "Bearer garbage"},
    )
    assert response.status_code == 200


# -- The declaration -----------------------------------------------------------------------


def test_F5_undeclared_routes_names_each_method_and_full_path_sorted() -> None:
    app = FastAPI()
    router = APIRouter()
    inner = APIRouter()

    async def noop() -> None:
        return None

    async def nested(_: Annotated[Caller, Depends(signed_in)]) -> None:
        return None

    inner.add_api_route("/b", noop, methods=["GET"])
    inner.add_api_route("/ok", noop, methods=["GET"], dependencies=[Depends(public)])
    router.include_router(inner, prefix="/inner")
    router.add_api_route("/a", noop, methods=["POST", "GET"])
    router.add_api_route("/deep", noop, methods=["GET"], dependencies=[Depends(nested)])
    app.include_router(router, prefix="/v1")
    app.add_api_route("/top", noop, methods=["GET"])
    app.add_api_route("/declared", noop, methods=["GET"], dependencies=[Depends(public)])
    app.mount("/static", Starlette(routes=[Mount("/x", routes=[])]))

    assert undeclared_routes(app) == [
        "GET /top",
        "GET /v1/a",
        "GET /v1/inner/b",
        "POST /v1/a",
    ]


def test_F5_a_capability_dependency_registered_with_declares_access_declares() -> None:
    @declares_access
    async def capability() -> None:
        return None

    app = FastAPI()

    async def noop() -> None:
        return None

    app.add_api_route("/c", noop, methods=["GET"], dependencies=[Depends(capability)])
    assert undeclared_routes(app) == []
    access._ACCESS_DEPENDENCIES.discard(capability)
    assert undeclared_routes(app) == ["GET /c"]
