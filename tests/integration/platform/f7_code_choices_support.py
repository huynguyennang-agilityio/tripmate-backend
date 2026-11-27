"""Probe routes for the tests of the code agent's own F7 choices (not locked).

The probe of `f7_support.py` plus two routes: `returns-4xx` (the handler returns a 409
`JSONResponse` after writing) and `slow-once` (the first call waits for RELEASE, as `slow` does).
"""

from __future__ import annotations

from typing import Any

from tests.integration.platform import f7_support
from tests.integration.platform.f7_support import Probe
from tests.support import APP_DIR

EXTRA_ROUTES = """

@router.post("/__SEGMENT__/slow-once", status_code=201, dependencies=OPEN)
async def slow_once(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("slow-once")
    if CALLS.count("slow-once") == 1:
        STARTED.set()
        await RELEASE.wait()
    record_id = await write_record(session, response)
    return {"id": record_id, "name": record.name}


@router.post("/__SEGMENT__/returns-4xx", status_code=201, dependencies=OPEN)
async def returns_4xx(record: Record, session: Session, response: Response) -> Response:
    CALLS.append("returns-4xx")
    record_id = await write_record(session, response)
    return JSONResponse(status_code=409, content={"reason": "returned", "id": record_id})
"""


def write_choices_probe(module_folder: Any, table: str) -> Probe:
    """Create the probe module of `f7_support` with the two extra routes."""
    name = module_folder({"__init__.py": f7_support.INIT_SOURCE})
    source = (
        (
            f7_support.API_SOURCE.replace(
                "from fastapi import APIRouter",
                "from fastapi.responses import JSONResponse\nfrom fastapi import APIRouter",
                1,
            )
            + EXTRA_ROUTES
        )
        .replace("__TABLE__", table)
        .replace("__SEGMENT__", name.replace("_", "-"))
        .replace("__NAME__", name)
    )
    (APP_DIR / name / "api.py").write_text(source, encoding="utf-8")
    return Probe(name)
