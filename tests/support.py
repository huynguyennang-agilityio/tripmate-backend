"""Plain helpers shared by the tests. The interface they rely on is in tests/INTERFACE.md."""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_DIR = REPO_ROOT / "app"

# The settings of tests/INTERFACE.md, section "Settings".
REQUIRED_SETTINGS = ("DATABASE_URL", "REDIS_URL", "RELEASE_VERSION")
ALL_SETTINGS = (*REQUIRED_SETTINGS, "PORT")

# Where the real services are, given to the test run (not to the app).
TEST_DATABASE_URL = "TEST_DATABASE_URL"
TEST_REDIS_URL = "TEST_REDIS_URL"

# The three process commands of tests/INTERFACE.md, section "Processes".
PROCESS_MODULES = {
    "api": "app.main",
    "worker": "app.worker",
    "scheduler": "app.scheduler",
}

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def free_port() -> int:
    """A local TCP port with nothing listening on it."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def made_up_password() -> str:
    """A throwaway value used to check that settings values are never printed."""
    return f"pw{uuid.uuid4().hex}"


def unreachable_database_url(password: str | None = None) -> str:
    """A PostgreSQL address on a closed local port."""
    password = password or made_up_password()
    return f"postgresql+asyncpg://tripmate:{password}@127.0.0.1:{free_port()}/tripmate"


def unreachable_redis_url() -> str:
    """A Redis address on a closed local port."""
    return f"redis://127.0.0.1:{free_port()}/0"


def service_url(name: str) -> str:
    """The address of a real service; a missing one fails the test, it never skips."""
    value = os.environ.get(name)
    if not value:
        pytest.fail(
            f"{name} is not set. This test needs the real service (tests/INTERFACE.md). "
            "A missing service is a failure, not a skip."
        )
    return value


def real_database_url() -> str:
    """Address of the real PostgreSQL the tests run against."""
    return service_url(TEST_DATABASE_URL)


def real_redis_url() -> str:
    """Address of the real Redis the tests run against."""
    return service_url(TEST_REDIS_URL)


def process_env(settings: dict[str, str]) -> dict[str, str]:
    """The environment of a started process: this one, minus every app setting, plus `settings`."""
    env = {k: v for k, v in os.environ.items() if k not in ALL_SETTINGS}
    env.update(settings)
    return env


def build_app() -> Any:
    """Build the API app from the current environment."""
    from app.main import create_app

    return create_app()


def route_paths(app: Any) -> list[str]:
    return sorted(getattr(route, "path", "") for route in app.routes)


@contextlib.asynccontextmanager
async def client_for(app: Any) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client on the app, with the app's start-up and shut-down run."""
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            yield client
