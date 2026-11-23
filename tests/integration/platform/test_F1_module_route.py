"""F1 Backend skeleton and health check: case F1-7 (epic-11 spec, section 7.1)."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from tests.support import APP_DIR, REPO_ROOT, build_app, client_for

API_SOURCE = '''\
from fastapi import APIRouter

router = APIRouter()


@router.get("/{suffix}")
async def probe() -> dict[str, str]:
    return {{"probe": "{suffix}"}}
'''


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
    set_settings()
    before = _repository_files()
    assert before, "no files found to compare"

    name = module_folder({"__init__.py": ""})
    suffix = name.replace("_", "-")
    (APP_DIR / name / "api.py").write_text(API_SOURCE.format(suffix=suffix), encoding="utf-8")

    app = build_app()
    # The spec does not say under which prefix a module's routes are mounted,
    # so the route is looked up by its last segment.
    paths = [p for p in (getattr(r, "path", "") for r in app.routes) if p.endswith(f"/{suffix}")]
    assert len(paths) == 1, f"expected one route ending in /{suffix}, found {paths}"

    async with client_for(app) as client:
        response = await client.get(paths[0])

    assert response.status_code == 200
    assert response.json() == {"probe": suffix}
    # Adding the module changed no file outside its folder.
    assert _repository_files(skip=APP_DIR / name) == before
