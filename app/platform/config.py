"""Settings: read from environment variables once at start-up, and checked.

No `.env` file is read. An error names the setting and never prints a value,
because values hold passwords (EPIC-11 spec, section 2.7).
"""

from __future__ import annotations

import os
import re
import sys
from typing import Annotated, Any
from urllib.parse import urlsplit

from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    ValidationError,
    field_validator,
)
from redis import ConnectionPool
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# Setting name -> field name. Every name is listed in `.env.example`.
SETTING_NAMES = {
    "DATABASE_URL": "database_url",
    "REDIS_URL": "redis_url",
    "RELEASE_VERSION": "release_version",
    "PORT": "port",
    "ACCESS_TOKEN_PUBLIC_KEYS": "access_token_public_keys",
}

DEFAULT_PORT = 8000

# The SSL modes asyncpg takes in its `ssl` parameter, named as libpq's `sslmode`.
SSL_MODES = frozenset({"disable", "allow", "prefer", "require", "verify-ca", "verify-full"})


class SettingsError(Exception):
    """The settings are missing or wrong. The message names settings, never values."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Settings are not valid: " + "; ".join(problems))


def _checked_database_url(secret: SecretStr) -> SecretStr:
    # Every message here is fixed text: a parser's own message can quote the value.
    try:
        url = make_url(secret.get_secret_value())
    except (ArgumentError, ValueError):
        raise ValueError("not a valid database address") from None
    # Render gives `postgresql://`; the app always talks to it through asyncpg.
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+asyncpg")
    if url.drivername != "postgresql+asyncpg":
        raise ValueError("must be a postgresql+asyncpg:// address")
    # asyncpg takes no libpq parameter: libpq's `sslmode` becomes asyncpg's `ssl`,
    # and any other parameter would fail only at the first connection.
    query = dict(url.query)
    if "sslmode" in query:
        if "ssl" in query:
            raise ValueError("takes ssl or sslmode, not both")
        query["ssl"] = query.pop("sslmode")
    if set(query) - {"ssl"}:
        raise ValueError("takes no query parameter other than ssl or sslmode")
    if "ssl" in query and query["ssl"] not in SSL_MODES:
        raise ValueError("ssl or sslmode must be one of " + ", ".join(sorted(SSL_MODES)))
    url = url.set(query=query)
    return SecretStr(url.render_as_string(hide_password=False))


def _checked_redis_url(secret: SecretStr) -> SecretStr:
    value = secret.get_secret_value()
    if not value.startswith(("redis://", "rediss://")):
        raise ValueError("must be a redis:// or rediss:// address")
    try:
        ConnectionPool.from_url(value)  # parses only; nothing connects
    except ValueError:
        raise ValueError("not a valid Redis address") from None
    # redis-py ignores a database number it cannot read, so check it here.
    database = urlsplit(value).path.removeprefix("/")
    if database and not (database.isascii() and database.isdigit()):
        raise ValueError("the database after the last / must be a whole number")
    return secret


_PEM_BLOCK = re.compile(r"-----BEGIN PUBLIC KEY-----.*?-----END PUBLIC KEY-----", re.DOTALL)


def _checked_public_keys(value: Any) -> tuple[str, ...]:
    """One or more Ed25519 public keys in PEM, one after another; each is returned as one PEM."""
    if not isinstance(value, str):
        raise ValueError("must be text")
    blocks = _PEM_BLOCK.findall(value)
    # Whatever lies between the blocks must be blank, so a damaged block is not skipped.
    if not blocks or _PEM_BLOCK.sub("", value).strip():
        raise ValueError("must hold only PEM public keys (BEGIN PUBLIC KEY)")
    for block in blocks:
        try:
            key = load_pem_public_key(block.encode("ascii", errors="replace"))
        except (ValueError, UnsupportedAlgorithm):
            raise ValueError("holds a public key that cannot be read") from None
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError("holds a public key that is not an Ed25519 key")
    return tuple(blocks)


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    # Both addresses can hold a password: `SecretStr` keeps them out of `repr()`
    # and logs. They are unwrapped only where the engine and the Redis client are made.
    database_url: Annotated[SecretStr, Field(min_length=1)]
    redis_url: Annotated[SecretStr, Field(min_length=1)]
    release_version: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    port: Annotated[int, Field(ge=1, le=65535)] = DEFAULT_PORT
    # Public keys, not secrets. Optional: without one, every route that is not public
    # answers `401 auth.not_signed_in` (EPIC-11 spec, section 2.2).
    access_token_public_keys: tuple[str, ...] = ()

    @field_validator("access_token_public_keys", mode="before")
    @classmethod
    def _ed25519_public_keys(cls, value: Any) -> tuple[str, ...]:
        return _checked_public_keys(value)

    @field_validator("database_url")
    @classmethod
    def _postgresql_with_asyncpg(cls, secret: SecretStr) -> SecretStr:
        return _checked_database_url(secret)

    @field_validator("redis_url")
    @classmethod
    def _redis(cls, secret: SecretStr) -> SecretStr:
        return _checked_redis_url(secret)


class DatabaseSettings(BaseModel):
    """Only the database address, for what needs no other setting (Alembic)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    database_url: Annotated[SecretStr, Field(min_length=1)]

    @field_validator("database_url")
    @classmethod
    def _postgresql_with_asyncpg(cls, secret: SecretStr) -> SecretStr:
        return _checked_database_url(secret)


def _problem(error: Any) -> str:
    field = str(error["loc"][0]) if error["loc"] else ""
    name = next((n for n, f in SETTING_NAMES.items() if f == field), field)
    if error["type"] == "missing":
        return f"{name} is missing"
    # `msg` describes the rule that failed; the value is in `input`, which is never shown.
    return f"{name} is not valid: {error['msg']}"


def load_settings(environ: dict[str, str] | None = None) -> Settings:
    """Read and check the settings. Raises SettingsError naming each wrong setting."""
    source = os.environ if environ is None else environ
    values = {field: source[name] for name, field in SETTING_NAMES.items() if source.get(name)}
    try:
        return Settings.model_validate(values)
    except ValidationError as error:
        raise SettingsError([_problem(e) for e in error.errors(include_input=False)]) from None


def load_settings_or_exit() -> Settings:
    """For a process entry point: the settings, or exit with code 2 naming what is wrong."""
    try:
        return load_settings()
    except SettingsError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2) from None


def load_database_url_or_exit() -> str:
    """For a process that needs only the database (Alembic): its checked address, or exit 2.

    Reads and checks `DATABASE_URL` as `load_settings` does, and no other setting.
    The message names the setting and never its value.
    """
    value = os.environ.get("DATABASE_URL")
    try:
        return DatabaseSettings.model_validate(
            {"database_url": value} if value else {}
        ).database_url.get_secret_value()
    except ValidationError as error:
        problems = [_problem(e) for e in error.errors(include_input=False)]
        print(str(SettingsError(problems)), file=sys.stderr)
        raise SystemExit(2) from None
