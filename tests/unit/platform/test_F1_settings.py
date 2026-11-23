"""F1 Backend skeleton and health check: cases F1-4 and F1-5 (epic-11 spec, section 7.1).

The API process is started with a broken environment. No PostgreSQL or Redis
is needed: both addresses point at closed local ports, and the process must
stop before it tries to reach them.
"""

from __future__ import annotations

import subprocess
import sys

import pytest

from tests.support import (
    PROCESS_MODULES,
    REPO_ROOT,
    REQUIRED_SETTINGS,
    free_port,
    made_up_password,
    process_env,
    unreachable_database_url,
    unreachable_redis_url,
)

STOP_TIMEOUT_SECONDS = 30


def _valid_settings(password: str) -> dict[str, str]:
    return {
        "DATABASE_URL": unreachable_database_url(password),
        "REDIS_URL": unreachable_redis_url(),
        "RELEASE_VERSION": f"f1-release-{made_up_password()}",
        "PORT": str(free_port()),
    }


def _start_api(settings: dict[str, str]) -> tuple[int, str]:
    """Start the API process; return its exit code and everything it printed.

    Fails when the process is still running after the timeout.
    """
    try:
        done = subprocess.run(
            [sys.executable, "-m", PROCESS_MODULES["api"]],
            cwd=REPO_ROOT,
            env=process_env(settings),
            capture_output=True,
            text=True,
            timeout=STOP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as still_running:
        raise AssertionError(
            f"the process did not stop within {STOP_TIMEOUT_SECONDS}s"
        ) from still_running
    return done.returncode, done.stdout + done.stderr


@pytest.mark.parametrize("missing", REQUIRED_SETTINGS)
def test_F1_4_missing_required_setting_stops_the_process_and_names_it(missing: str) -> None:
    password = made_up_password()
    settings = _valid_settings(password)
    del settings[missing]

    exit_code, output = _start_api(settings)

    assert exit_code != 0, f"started without {missing}"
    assert missing in output, f"{missing} is not named in: {output}"
    for name, value in settings.items():
        assert value not in output, f"the value of {name} is printed: {output}"
    assert password not in output, f"the database password is printed: {output}"


def test_F1_5_setting_of_the_wrong_type_stops_the_process_and_names_it() -> None:
    settings = _valid_settings(made_up_password())
    settings["PORT"] = "not-a-number"

    exit_code, output = _start_api(settings)

    assert exit_code != 0, "started with PORT=not-a-number"
    assert "PORT" in output, f"PORT is not named in: {output}"
