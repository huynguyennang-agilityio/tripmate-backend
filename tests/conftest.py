"""Fixtures shared by the tests. The interface they rely on is in tests/INTERFACE.md."""

from __future__ import annotations

import importlib
import shutil
import sys
import uuid
from collections.abc import Callable, Iterator

import pytest

from tests.support import (
    APP_DIR,
    free_port,
    unreachable_database_url,
    unreachable_redis_url,
)


@pytest.fixture
def set_settings(monkeypatch: pytest.MonkeyPatch) -> Callable[..., None]:
    """Put a full, valid set of settings in the environment of this test.

    PostgreSQL and Redis point at closed local ports unless the test passes
    real addresses.
    """

    def _set(**overrides: str) -> None:
        values = {
            "DATABASE_URL": unreachable_database_url(),
            "REDIS_URL": unreachable_redis_url(),
            "RELEASE_VERSION": "f1-test",
            "PORT": str(free_port()),
        }
        values.update(overrides)
        for name, value in values.items():
            monkeypatch.setenv(name, value)

    return _set


@pytest.fixture
def module_folder() -> Iterator[Callable[[dict[str, str]], str]]:
    """Create a temporary module folder under app/ and remove it after the test.

    Takes {file name: source} and returns the folder (package) name.
    """
    created: list[str] = []

    def _create(files: dict[str, str]) -> str:
        assert APP_DIR.is_dir(), f"{APP_DIR} does not exist"
        name = f"f1probe_{uuid.uuid4().hex[:8]}"
        folder = APP_DIR / name
        folder.mkdir()
        created.append(name)
        for file_name, source in files.items():
            (folder / file_name).write_text(source, encoding="utf-8")
        importlib.invalidate_caches()
        return name

    try:
        yield _create
    finally:
        for name in created:
            shutil.rmtree(APP_DIR / name, ignore_errors=True)
            package = f"app.{name}"
            for loaded in [m for m in sys.modules if m == package or m.startswith(f"{package}.")]:
                del sys.modules[loaded]
        importlib.invalidate_caches()
