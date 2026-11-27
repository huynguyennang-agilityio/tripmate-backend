"""F1 Backend skeleton and health check: case F1-7 (epic-11 spec, section 7.1).

The route is checked through HTTP and the app's public OpenAPI document,
never through FastAPI's internal route list.
"""

from __future__ import annotations

import hashlib
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.support import APP_DIR, REPO_ROOT, build_app, client_for, openapi_paths

# tests/INTERFACE.md, section "Modules found by scanning".
MODULE_PREFIX = "/v1"

API_SOURCE = """\
from fastapi import APIRouter

router = APIRouter()


@router.get("/{segment}")
async def probe() -> dict[str, str]:
    return {{"probe": "{segment}"}}
"""


def _repository_files(skip: Path | None = None) -> dict[str, str]:
    """Hash of every file git does not ignore, leaving out `skip` and bytecode caches."""
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO_ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    hashes: dict[str, str] = {}
    for relative in filter(None, listed.split("\0")):
        path = REPO_ROOT / relative
        if skip is not None and skip in path.parents:
            continue
        if "__pycache__" in path.parts or not path.is_file():
            continue
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


@pytest.mark.asyncio
async def test_F1_7_new_module_folder_with_api_py_gets_its_route_served(
    set_settings, module_folder
) -> None:
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    before = _repository_files()
    assert before, "no files found to compare"
    app_without_folder = build_app()

    name = module_folder({"__init__.py": ""})
    segment = name.replace("_", "-")
    path = f"{MODULE_PREFIX}/{segment}"
    (APP_DIR / name / "api.py").write_text(API_SOURCE.format(segment=segment), encoding="utf-8")

    # X-Device-Id is asked for on every request but the health check (F5, spec section 2.2).
    headers = {"X-Device-Id": str(uuid.uuid4())}
    app = build_app()
    async with client_for(app) as client:
        response = await client.get(path, headers=headers)
    assert response.status_code == 200, f"GET {path}: {response.status_code} {response.text}"
    assert response.json() == {"probe": segment}

    ending = [p for p in openapi_paths(app) if p.endswith(f"/{segment}")]
    assert ending == [path], f"expected only {path} in the OpenAPI paths, found {ending}"

    async with client_for(app_without_folder) as client:
        response = await client.get(path, headers=headers)
    assert response.status_code == 404, f"GET {path} without the folder: {response.status_code}"

    # Adding the module changed no file outside its folder.
    assert _repository_files(skip=APP_DIR / name) == before
