"""Helpers of the F12 tests: the stub module source and the contract command.

The interface they rely on is in tests/INTERFACE.md, section "F12: Contract publishing".
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from tests.support import REPO_ROOT, process_env

CONTRACT_MODULE = "app.platform.contract"

# tests/INTERFACE.md, section "Modules found by scanning".
MODULE_PREFIX = "/v1"

# The error code a stub returns (task F12, scope).
NOT_IMPLEMENTED = "server.not_implemented"

# The keys of the error format (api-design.md, section 4.1; app/platform/errors.py).
ERROR_KEYS = {"code", "params", "fields", "requestId"}

# One stub whose response model carries an example, and, when `bare` is given,
# a second stub whose response model has none.
STUB_API_SOURCE = """\
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.platform.contract import stub

router = APIRouter()


class WithExample(BaseModel):
    model_config = ConfigDict(json_schema_extra={{"examples": [{{"name": "Da Lat"}}]}})

    name: str


class WithoutExample(BaseModel):
    title: str


@stub(router, "GET", "/{segment}", response_model=WithExample)
async def with_example() -> WithExample: ...
{bare}"""

BARE_STUB_SOURCE = """

@stub(router, "GET", "/{segment}", response_model=WithoutExample)
async def without_example() -> WithoutExample: ...
"""


def stub_api_source(segment: str, bare_segment: str | None = None) -> str:
    bare = BARE_STUB_SOURCE.format(segment=bare_segment) if bare_segment else ""
    return STUB_API_SOURCE.format(segment=segment, bare=bare)


def generation_env(**extra: str) -> dict[str, str]:
    """No DATABASE_URL, REDIS_URL, or PORT: CI generates the file with no services."""
    return process_env({"RELEASE_VERSION": "f12-contract", **extra})


def run_contract(*args: str, **extra_env: str) -> subprocess.CompletedProcess[str]:
    """`<python> -m app.platform.contract <args>` from the repository root."""
    return subprocess.run(
        [sys.executable, "-m", CONTRACT_MODULE, *args],
        cwd=REPO_ROOT,
        env=generation_env(**extra_env),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )


def generate(path: Path, **extra_env: str) -> dict[str, Any]:
    """Generate the contract into `path` in a new process and return it read back as JSON."""
    result = run_contract("generate", str(path), **extra_env)
    assert result.returncode == 0, (
        f"generate exited {result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert path.is_file(), f"generate did not write {path}"
    contract: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return contract
