"""Finding the modules by scanning `app/` (EPIC-11 spec, section 2.1).

A module is a package directly under `app/`. When it has an `api.py`, the
`router` defined there is served under `/v1`. Adding a module therefore
changes no shared file, and `platform` never imports a module by name.
"""

from __future__ import annotations

import importlib
from pathlib import Path

from fastapi import APIRouter, FastAPI

APP_DIR = Path(__file__).resolve().parent.parent
API_PREFIX = "/v1"
NOT_MODULES = frozenset({"platform"})


def module_names() -> list[str]:
    """The packages directly under `app/`, other than `platform`, in name order."""
    return sorted(
        path.name
        for path in APP_DIR.iterdir()
        if path.is_dir()
        and path.name.isidentifier()
        and path.name not in NOT_MODULES
        and (path / "__init__.py").is_file()
    )


def include_module_routes(app: FastAPI) -> None:
    """Serve the `router` of every module that has an `api.py`."""
    for name in module_names():
        if not (APP_DIR / name / "api.py").is_file():
            continue
        api = importlib.import_module(f"app.{name}.api")
        router = getattr(api, "router", None)
        if not isinstance(router, APIRouter):
            raise TypeError(f"app/{name}/api.py must define `router`, an APIRouter")
        app.include_router(router, prefix=API_PREFIX)
