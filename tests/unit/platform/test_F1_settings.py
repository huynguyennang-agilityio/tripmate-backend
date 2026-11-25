"""F1 Backend skeleton and health check: cases F1-4 and F1-5 (epic-11 spec, sections 2.7 and 7.1).

Each process of tests/INTERFACE.md, section "Processes" (API, worker, and
scheduler), is started with a broken environment. No PostgreSQL or Redis is
needed: both addresses point at closed local ports, and the process must stop
before it tries to reach them.

`PORT` is the port the API listens on, so its wrong-type case runs for the API
only; every other case runs for all three processes.
"""

from __future__ import annotations

import subprocess
import sys
import uuid

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
PROCESSES = tuple(PROCESS_MODULES)


def _valid_settings(password: str) -> dict[str, str]:
    return {
        "DATABASE_URL": unreachable_database_url(password),
        "REDIS_URL": unreachable_redis_url(),
        "RELEASE_VERSION": f"f1-release-{made_up_password()}",
        "PORT": str(free_port()),
    }


def _start(process: str, settings: dict[str, str]) -> tuple[int, str]:
    """Start one process; return its exit code and everything it printed.

    Fails when the process is still running after the timeout.
    """
    try:
        done = subprocess.run(
            [sys.executable, "-m", PROCESS_MODULES[process]],
            cwd=REPO_ROOT,
            env=process_env(settings),
            capture_output=True,
            text=True,
            timeout=STOP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as still_running:
        raise AssertionError(
            f"the {process} process did not stop within {STOP_TIMEOUT_SECONDS}s"
        ) from still_running
    return done.returncode, done.stdout + done.stderr


@pytest.mark.parametrize("process", PROCESSES)
@pytest.mark.parametrize("missing", REQUIRED_SETTINGS)
def test_F1_4_missing_required_setting_stops_the_process_and_names_it(
    process: str, missing: str
) -> None:
    password = made_up_password()
    settings = _valid_settings(password)
    del settings[missing]

    exit_code, output = _start(process, settings)

    assert exit_code != 0, f"the {process} process started without {missing}"
    assert missing in output, f"{missing} is not named in: {output}"
    for name, value in settings.items():
        assert value not in output, f"the value of {name} is printed: {output}"
    assert password not in output, f"the database password is printed: {output}"


def _wrong_type_cases() -> list[tuple[str, str, list[str]]]:
    """(setting, wrong value, parts of the value that must not be printed).

    The parts are random, so finding one in the output can only mean it was printed.
    """
    password = made_up_password()
    host = f"db-{uuid.uuid4().hex[:12]}.example"
    port = f"notaport{uuid.uuid4().hex[:8]}"
    redis_host = f"redis-{uuid.uuid4().hex[:12]}.example"
    return [
        ("PORT", "not-a-number", ["not-a-number"]),
        # The "@" between the password and the host is missing.
        (
            "DATABASE_URL",
            f"postgresql://tripmate:{password}.{host}/tripmate",
            [password, host],
        ),
        # The port is not a number.
        (
            "DATABASE_URL",
            f"postgresql+asyncpg://tripmate:{password}@{host}:{port}/tripmate",
            [password, host, port],
        ),
        # The port is not a number.
        ("REDIS_URL", f"redis://{redis_host}:{port}/0", [redis_host, port]),
    ]


def _wrong_type_cases_per_process() -> list[pytest.ParameterSet]:
    """Each wrong-type case for each process; `PORT` only for the API, the one process
    that listens on it."""
    ids = ["PORT", "DATABASE_URL-no-at", "DATABASE_URL-port", "REDIS_URL-port"]
    return [
        pytest.param(process, setting, value, secret_parts, id=f"{process}-{case_id}")
        for process in PROCESSES
        for case_id, (setting, value, secret_parts) in zip(ids, _wrong_type_cases(), strict=True)
        if setting != "PORT" or process == "api"
    ]


@pytest.mark.parametrize(
    ("process", "setting", "value", "secret_parts"),
    _wrong_type_cases_per_process(),
)
def test_F1_5_setting_of_the_wrong_type_stops_the_process_and_names_it(
    process: str, setting: str, value: str, secret_parts: list[str]
) -> None:
    settings = _valid_settings(made_up_password())
    settings[setting] = value

    exit_code, output = _start(process, settings)

    assert exit_code != 0, f"the {process} process started with a wrong {setting}"
    assert setting in output, f"{setting} is not named in: {output}"
    assert value not in output, f"the value of {setting} is printed: {output}"
    for part in secret_parts:
        assert part not in output, f"part of the value of {setting} is printed: {output}"
