"""F5 The setting ACCESS_TOKEN_PUBLIC_KEYS (code agent's own tests, EPIC-11 spec, section 2.2)."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app.platform.config import SettingsError, load_settings

BASE = {
    "DATABASE_URL": "postgresql+asyncpg://u:p@127.0.0.1:5432/d",
    "REDIS_URL": "redis://127.0.0.1:6379/0",
    "RELEASE_VERSION": "x",
}
FORMAT = serialization.PublicFormat.SubjectPublicKeyInfo
PEM = serialization.Encoding.PEM


def _ed25519_pem() -> str:
    return Ed25519PrivateKey.generate().public_key().public_bytes(PEM, FORMAT).decode()


def test_F5_keys_are_optional() -> None:
    assert load_settings(dict(BASE)).access_token_public_keys == ()


def test_F5_keys_one_after_another_are_all_kept() -> None:
    first, second = _ed25519_pem(), _ed25519_pem()
    settings = load_settings({**BASE, "ACCESS_TOKEN_PUBLIC_KEYS": first + second})
    assert settings.access_token_public_keys == (first.strip(), second.strip())


def test_F5_a_key_that_is_not_ed25519_or_not_a_key_names_the_setting_and_not_the_value() -> None:
    rsa_pem = rsa.generate_private_key(65537, 2048).public_key().public_bytes(PEM, FORMAT).decode()
    damaged = "-----BEGIN PUBLIC KEY-----\nsecretvalue\n-----END PUBLIC KEY-----\n"
    for value in (rsa_pem, damaged, "not a key secretvalue", _ed25519_pem() + "junk secretvalue"):
        with pytest.raises(SettingsError) as error:
            load_settings({**BASE, "ACCESS_TOKEN_PUBLIC_KEYS": value})
        assert "ACCESS_TOKEN_PUBLIC_KEYS" in str(error.value)
        assert "secretvalue" not in str(error.value)
