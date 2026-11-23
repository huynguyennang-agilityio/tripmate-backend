"""Settings: read from environment variables once at start-up, and checked.

No `.env` file is read. An error names the setting and never prints a value,
because values hold passwords (EPIC-11 spec, section 2.7).
"""

from __future__ import annotations

import os
import sys
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

# Setting name -> field name. Every name is listed in `.env.example`.
SETTING_NAMES = {
    "DATABASE_URL": "database_url",
    "REDIS_URL": "redis_url",
    "RELEASE_VERSION": "release_version",
    "PORT": "port",
}

DEFAULT_PORT = 8000


class SettingsError(Exception):
    """The settings are missing or wrong. The message names settings, never values."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("Settings are not valid: " + "; ".join(problems))


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    database_url: Annotated[str, Field(min_length=1)]
    redis_url: Annotated[str, Field(min_length=1)]
    release_version: Annotated[str, Field(min_length=1)]
    port: Annotated[int, Field(ge=1, le=65535)] = DEFAULT_PORT

    @field_validator("database_url")
    @classmethod
    def _postgresql_with_asyncpg(cls, value: str) -> str:
        try:
            url = make_url(value)
        except ArgumentError:
            raise ValueError("not a valid database address") from None
        # Render gives `postgresql://`; the app always talks to it through asyncpg.
        if url.drivername == "postgresql":
            url = url.set(drivername="postgresql+asyncpg")
        if url.drivername != "postgresql+asyncpg":
            raise ValueError("must be a postgresql+asyncpg:// address")
        return url.render_as_string(hide_password=False)

    @field_validator("redis_url")
    @classmethod
    def _redis(cls, value: str) -> str:
        if not value.startswith(("redis://", "rediss://")):
            raise ValueError("must be a redis:// or rediss:// address")
        return value


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
