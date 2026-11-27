"""F5 round 1 (code agent's own tests): the contract of the access check, and keys parsed once.

R1-1 the header `X-Device-Id` and the `400`; R1-2 the bearer scheme and the `401`;
R1-5 the keys parsed once at start-up. No PostgreSQL or Redis.
"""

from __future__ import annotations

import time
import uuid
from typing import Annotated, Any

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from fastapi import Depends, FastAPI

from app.platform import access
from app.platform.access import (
    Caller,
    install_access_contract,
    install_access_keys,
    install_device_id,
    parse_public_keys,
    public,
    signed_in,
)
from app.platform.config import load_settings
from app.platform.errors import install_error_handlers, install_openapi, install_request_id
from app.platform.modules import Registry

ENV = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/d",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "RELEASE_VERSION": "x",
    "RATE_LIMIT_PER_MINUTE": "0",
}
PEM = serialization.Encoding.PEM
FORMAT = serialization.PublicFormat.SubjectPublicKeyInfo
DEVICE_REF = {"$ref": "#/components/parameters/DeviceId"}
ERROR_REF = {"$ref": "#/components/schemas/ErrorResponse"}


async def inner(caller: Annotated[Caller, Depends(signed_in)]) -> Caller:
    return caller


def _pem(key: Ed25519PrivateKey) -> str:
    return key.public_key().public_bytes(PEM, FORMAT).decode()


def _app(key: Ed25519PrivateKey) -> FastAPI:
    app = FastAPI()
    install_error_handlers(app)
    install_openapi(app)
    install_access_contract(app)
    app.state.settings = load_settings({**ENV, "ACCESS_TOKEN_PUBLIC_KEYS": _pem(key)})
    install_access_keys(app, app.state.settings.access_token_public_keys)
    registry = Registry()

    async def active(session_id: uuid.UUID) -> bool:
        return True

    registry.set_session_hook(active)
    app.state.registry = registry

    @app.get("/v1/health", dependencies=[Depends(public)])
    async def health() -> dict[str, str]:
        return {}

    @app.get("/v1/open", dependencies=[Depends(public)])
    async def open_route() -> dict[str, str]:
        return {}

    @app.get("/v1/q", dependencies=[Depends(public)])
    async def query(page: int) -> dict[str, int]:
        return {"page": page}

    @app.get("/v1/me")
    async def me(caller: Annotated[Caller, Depends(signed_in)]) -> dict[str, str]:
        return {}

    @app.post("/v1/nested")
    async def nested(caller: Annotated[Caller, Depends(inner)]) -> dict[str, str]:
        return {}

    install_device_id(app)
    install_request_id(app)
    return app


def test_F5_R1_1_every_operation_but_health_declares_the_device_id_and_the_400() -> None:
    contract = _app(Ed25519PrivateKey.generate()).openapi()
    parameter = contract["components"]["parameters"]["DeviceId"]
    assert parameter["name"] == "X-Device-Id"
    assert parameter["in"] == "header"
    assert parameter["required"] is True
    assert parameter["schema"] == {"type": "string", "format": "uuid"}
    assert parameter["description"] == (
        "The ID of the device, a UUID the client makes once (security.md)."
    )
    operations = 0
    for path, item in contract["paths"].items():
        for method, operation in item.items():
            operations += 1
            if path == "/v1/health":
                assert DEVICE_REF not in operation.get("parameters", []), method
                assert "400" not in operation["responses"], method
            else:
                assert operation["parameters"][0] == DEVICE_REF, (method, path)
                schema = operation["responses"]["400"]["content"]["application/json"]["schema"]
                assert schema == ERROR_REF, (method, path)
                assert (
                    operation["responses"]["400"]["description"] == ("The request is not valid")
                    or path == "/v1/q"
                )
    assert operations == 5


def test_F5_R1_1_an_existing_400_stays_and_the_contract_is_changed_once() -> None:
    app = _app(Ed25519PrivateKey.generate())
    contract = app.openapi()
    assert app.openapi() is contract
    operation = contract["paths"]["/v1/q"]["get"]
    assert [p.get("$ref") or p["name"] for p in operation["parameters"]] == [
        DEVICE_REF["$ref"],
        "page",
    ]
    assert operation["responses"]["400"]["description"].endswith("(api-design.md, 4.1)")
    assert "422" not in operation["responses"]


def test_F5_R1_2_signed_in_routes_carry_the_bearer_security_and_the_401() -> None:
    contract = _app(Ed25519PrivateKey.generate()).openapi()
    assert contract["components"]["securitySchemes"] == {
        "bearerAuth": {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
    }
    for method, path in (("get", "/v1/me"), ("post", "/v1/nested")):  # nested: via a dependency
        operation = contract["paths"][path][method]
        assert operation["security"] == [{"bearerAuth": []}], path
        schema = operation["responses"]["401"]["content"]["application/json"]["schema"]
        assert schema == ERROR_REF, path
        assert operation["responses"]["401"]["description"] == "Not signed in", path
    for path in ("/v1/open", "/v1/q", "/v1/health"):
        operation = contract["paths"][path]["get"]
        assert "security" not in operation, path
        assert "401" not in operation["responses"], path


def test_F5_R1_5_keys_are_parsed_once_and_a_non_ed25519_key_fails_without_its_text() -> None:
    key = Ed25519PrivateKey.generate()
    app = _app(key)
    assert [k.public_bytes_raw() for k in app.state.access_keys] == [
        key.public_key().public_bytes_raw()
    ]
    assert parse_public_keys(()) == ()
    rsa_pem = rsa.generate_private_key(65537, 2048).public_key().public_bytes(PEM, FORMAT).decode()
    with pytest.raises(TypeError) as error:
        parse_public_keys((_pem(key), rsa_pem))
    assert str(error.value) == "ACCESS_TOKEN_PUBLIC_KEYS holds a public key that is not Ed25519"


@pytest.mark.asyncio
async def test_F5_R1_5_a_request_does_not_parse_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Ed25519PrivateKey.generate()
    app = _app(key)
    parsed: list[bytes] = []
    original = access.load_pem_public_key

    def spy(data: bytes, *args: Any) -> Any:
        parsed.append(data)
        return original(data, *args)

    monkeypatch.setattr(access, "load_pem_public_key", spy)
    now = int(time.time())
    claims = {"sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "iat": now, "exp": now + 900}
    headers = {
        "X-Device-Id": str(uuid.uuid4()),
        "Authorization": "Bearer " + jwt.encode(claims, key, algorithm="EdDSA"),
    }
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        for _ in range(3):
            assert (await c.get("/v1/me", headers=headers)).status_code == 200
    assert parsed == []
