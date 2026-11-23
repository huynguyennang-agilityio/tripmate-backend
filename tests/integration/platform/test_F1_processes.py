"""F1 Backend skeleton and health check: case F1-6 (epic-11 spec, section 7.1).

The three commands are the ones the one image runs (tests/INTERFACE.md,
section "Processes"). Docker is not needed: they run as local processes.
"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import httpx

from tests.support import (
    PROCESS_MODULES,
    REPO_ROOT,
    free_port,
    process_env,
    real_database_url,
    real_redis_url,
)

START_TIMEOUT_SECONDS = 30
STAY_UP_SECONDS = 5


def _output(tmp_path: Path, name: str) -> str:
    return (tmp_path / f"{name}.log").read_text(errors="replace")


def test_F1_6_api_worker_and_scheduler_each_start_and_stay_up(
    tmp_path: Path,
) -> None:
    database_url, redis_url = real_database_url(), real_redis_url()
    port = free_port()
    env = process_env(
        {
            "DATABASE_URL": database_url,
            "REDIS_URL": redis_url,
            "RELEASE_VERSION": "f1-test",
            "PORT": str(port),
        }
    )

    processes: dict[str, subprocess.Popen[bytes]] = {}
    try:
        for name, module in PROCESS_MODULES.items():
            with (tmp_path / f"{name}.log").open("wb") as log:
                processes[name] = subprocess.Popen(
                    [sys.executable, "-m", module],
                    cwd=REPO_ROOT,
                    env=env,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                )

        # The API has started when it answers HTTP on PORT.
        deadline = time.monotonic() + START_TIMEOUT_SECONDS
        answered = False
        while time.monotonic() < deadline and processes["api"].poll() is None:
            try:
                httpx.get(f"http://127.0.0.1:{port}/v1/health", timeout=2)
                answered = True
                break
            except httpx.TransportError:
                time.sleep(0.5)
        assert answered, f"the API did not answer on port {port}: {_output(tmp_path, 'api')}"

        time.sleep(STAY_UP_SECONDS)

        stopped = {
            name: (process.returncode, _output(tmp_path, name))
            for name, process in processes.items()
            if process.poll() is not None
        }
        assert not stopped, f"stopped (exit code, output): {stopped}"
    finally:
        for process in processes.values():
            if process.poll() is None:
                process.terminate()
        for process in processes.values():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
