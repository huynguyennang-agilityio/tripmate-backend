"""F5 `GET /v1/platform/whoami`, the checkpoint route (code agent's own tests)."""

from __future__ import annotations

import time
import uuid

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from tests.support import build_app, client_for

HOOK_SOURCE = """\
ENDED = set()


async def session_is_active(session_id):
    return str(session_id) not in ENDED


def register(registry):
    registry.set_session_hook(session_is_active)
"""


def _headers(key: Ed25519PrivateKey, **claims: object) -> dict[str, str]:
    now = int(time.time())
    body = {"sub": str(uuid.uuid4()), "sid": str(uuid.uuid4()), "iat": now, "exp": now + 900}
    body.update(claims)
    token = jwt.encode(body, key, algorithm="EdDSA")
    return {"X-Device-Id": str(uuid.uuid4()), "Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_F5_whoami_answers_the_caller_and_each_error_of_2_3(
    set_settings, monkeypatch, module_folder
) -> None:
    set_settings()
    key = Ed25519PrivateKey.generate()
    pem = key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    monkeypatch.setenv("ACCESS_TOKEN_PUBLIC_KEYS", pem.decode())
    module_folder({"__init__.py": HOOK_SOURCE})
    user, session = uuid.uuid4(), uuid.uuid4()

    async with client_for(build_app()) as client:
        ok = await client.get(
            "/v1/platform/whoami", headers=_headers(key, sub=str(user), sid=str(session))
        )
        none = await client.get("/v1/platform/whoami", headers={"X-Device-Id": str(uuid.uuid4())})
        expired = await client.get(
            "/v1/platform/whoami", headers=_headers(key, exp=int(time.time()) - 60)
        )

    assert ok.status_code == 200
    assert ok.json() == {"userId": str(user), "sessionId": str(session)}
    assert none.status_code == 401
    assert none.json()["error"]["code"] == "auth.not_signed_in"
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "auth.token_expired"
