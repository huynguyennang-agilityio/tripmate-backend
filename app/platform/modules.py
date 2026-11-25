"""Finding the modules by scanning `app/` (EPIC-11 spec, section 2.1).

A module is a package directly under `app/`. At start-up each process (the
API, the worker, and the scheduler) runs the same scan, `scan_modules()`,
which collects what every module registers into one `Registry`:

- the `router` of the module's `api.py`, when it has one, served under `/v1`;
- whatever the module's `register(registry)` hook adds, when its
  `__init__.py` defines one: jobs, event handlers, due-work queries, and
  error codes.

Migrations run a scan of their own, `import_models()`, which loads every
module's `models.py` into `Base.metadata`.

Adding a module therefore changes no shared file, and `platform` never
imports a module by name. Only the registration lives here: running the
jobs, handlers, and due-work queries comes with card F9, and checking the
error codes with card F4.
"""

from __future__ import annotations

import importlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from fastapi import APIRouter, FastAPI

APP_DIR = Path(__file__).resolve().parent.parent
API_PREFIX = "/v1"
NOT_MODULES = frozenset({"platform"})

# What a module registers. The arguments are settled by the cards that run
# them: jobs and event handlers by F9 (events by F8), due-work queries by F9.
Job = Callable[..., Awaitable[object]]
EventHandler = Callable[..., Awaitable[object]]
DueWorkQuery = Callable[..., Awaitable[object]]


@dataclass
class Registry:
    """Everything the modules registered at start-up, in module name order."""

    routers: list[APIRouter] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)
    # Event type (`<module>.<event>`) -> the handlers of other modules.
    event_handlers: dict[str, list[EventHandler]] = field(default_factory=dict)
    due_work: list[DueWorkQuery] = field(default_factory=list)
    # Codes of the form `<area>.<reason>` that the module can return.
    error_codes: set[str] = field(default_factory=set)

    def add_job(self, job: Job) -> None:
        self.jobs.append(job)

    def add_event_handler(self, event_type: str, handler: EventHandler) -> None:
        self.event_handlers.setdefault(event_type, []).append(handler)

    def add_due_work(self, query: DueWorkQuery) -> None:
        self.due_work.append(query)

    def add_error_codes(self, *codes: str) -> None:
        self.error_codes.update(codes)


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


def scan_modules() -> Registry:
    """Scan `app/` and collect what every module registers."""
    registry = Registry()
    for name in module_names():
        if (APP_DIR / name / "api.py").is_file():
            api = importlib.import_module(f"app.{name}.api")
            router = getattr(api, "router", None)
            if not isinstance(router, APIRouter):
                raise TypeError(f"app/{name}/api.py must define `router`, an APIRouter")
            registry.routers.append(router)
        register = getattr(importlib.import_module(f"app.{name}"), "register", None)
        if register is not None:
            if not callable(register):
                raise TypeError(f"app/{name}/__init__.py: `register` must be a function")
            register(registry)
    return registry


def import_models() -> list[str]:
    """Import every module's `models.py`, so its tables are in `Base.metadata`.

    Migrations call this to compare the models with the database. Returns the
    module names whose models were imported.
    """
    imported = []
    for name in module_names():
        if (APP_DIR / name / "models.py").is_file():
            importlib.import_module(f"app.{name}.models")
            imported.append(name)
    return imported


def include_routes(app: FastAPI, registry: Registry) -> None:
    """Serve the routers the modules registered, under `/v1`."""
    for router in registry.routers:
        app.include_router(router, prefix=API_PREFIX)
