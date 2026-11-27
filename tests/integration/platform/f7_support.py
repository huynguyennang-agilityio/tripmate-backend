"""Helpers of the F7 idempotency key tests (tests/INTERFACE.md, section "F7: Idempotency keys").

A temporary module under app/ (the `module_folder` fixture, as in F5 and F6) serves the probe
routes. Each one that declares `Depends(idempotent)` writes a record into a probe table of the
test's own (whose unique `marker` is checked only at commit, as in F3 round 1), records in
`CALLS` that its handler ran, and answers `201` with the record's ID and the headers
`Location`, `ETag`, and `X-Probe-Extra`. Routes named in the module's `FAILING` set fail. The
rows of `idempotency_keys` are read through a connection of the test's own, with the columns of
data-model.md, section 6.3.
"""

from __future__ import annotations

import importlib
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from tests.api.platform.f6_support import new_private_key, public_pem, token_for
from tests.support import (
    APP_DIR,
    database_engine,
    real_database_url,
    real_redis_url,
    run_alembic,
)

MODULE_PREFIX = "/v1"
DEVICE_HEADER = "X-Device-Id"
KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
ERROR_KEYS = {"code", "params", "fields", "requestId"}

# A step that waits longer than this has hung.
STEP_TIMEOUT = 15.0

KEY_REQUIRED = "request.idempotency_key_required"
MISMATCH = "request.idempotency_mismatch"
IN_PROGRESS = "request.idempotency_in_progress"
REQUEST_INVALID = "request.invalid"
REFUSED = "f7probe.refused"

INIT_SOURCE = """\
import asyncio
import uuid

# The name of each handler that ran, in order.
CALLS: list[str] = []
# The routes that fail while their name is here.
FAILING: set[str] = set()
# `slow` sets STARTED when its handler runs, then waits for RELEASE.
STARTED = asyncio.Event()
RELEASE = asyncio.Event()


async def session_is_active(session_id: uuid.UUID) -> bool:
    return True


def register(registry):
    registry.set_session_hook(session_is_active)
"""

# __TABLE__, __SEGMENT__, and __NAME__ are replaced when the module is written.
API_SOURCE = """\
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.access import Caller, public, signed_in
from app.platform.db import get_session
from app.platform.errors import AppError
from app.platform.idempotency import idempotent
from app.platform.rate_limits import rate_limit

from . import CALLS, FAILING, RELEASE, STARTED

router = APIRouter()

INSERT = text("INSERT INTO __TABLE__ (marker) VALUES (:marker)")
Session = Annotated[AsyncSession, Depends(get_session)]
OPEN = [Depends(public), Depends(idempotent)]


class Record(BaseModel):
    name: str
    amount: int


def probe_key(request: Request) -> str:
    return "__NAME__:limited"


async def write_record(session: AsyncSession, response: Response, twice: bool = False) -> str:
    record_id = str(uuid.uuid4())
    await session.execute(INSERT, {"marker": record_id})
    if twice:
        # The deferred unique marker fails only at commit.
        await session.execute(INSERT, {"marker": record_id})
    response.headers["Location"] = "/v1/__SEGMENT__/records/" + record_id
    response.headers["ETag"] = '"' + record_id[:8] + '"'
    response.headers["X-Probe-Extra"] = "not-stored"
    return record_id


@router.post("/__SEGMENT__/records", status_code=201, dependencies=OPEN)
async def create_record(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("records")
    record_id = await write_record(session, response)
    return {"id": record_id, "name": record.name, "amount": str(record.amount)}


@router.put("/__SEGMENT__/records", status_code=201, dependencies=OPEN)
async def put_record(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("put-records")
    record_id = await write_record(session, response)
    return {"id": record_id, "name": record.name}


@router.post("/__SEGMENT__/others", status_code=201, dependencies=OPEN)
async def create_other(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("others")
    record_id = await write_record(session, response)
    return {"id": record_id, "name": record.name}


@router.post("/__SEGMENT__/refused", status_code=201, dependencies=OPEN)
async def refuse(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("refused")
    await write_record(session, response)
    raise AppError(422, "f7probe.refused", params={"name": record.name})


@router.post("/__SEGMENT__/crashes", status_code=201, dependencies=OPEN)
async def crash(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("crashes")
    record_id = await write_record(session, response)
    if "crashes" in FAILING:
        raise RuntimeError("F7 probe: the handler raises after writing")
    return {"id": record_id, "name": record.name}


@router.post("/__SEGMENT__/unavailable", status_code=201, dependencies=OPEN)
async def unavailable(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("unavailable")
    record_id = await write_record(session, response)
    if "unavailable" in FAILING:
        raise AppError(503, "server.unavailable")
    return {"id": record_id, "name": record.name}


@router.post("/__SEGMENT__/commit-fails", status_code=201, dependencies=OPEN)
async def commit_fails(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("commit-fails")
    record_id = await write_record(session, response, twice="commit-fails" in FAILING)
    return {"id": record_id, "name": record.name}


@router.post("/__SEGMENT__/slow", status_code=201, dependencies=OPEN)
async def slow(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("slow")
    STARTED.set()
    await RELEASE.wait()
    record_id = await write_record(session, response)
    return {"id": record_id, "name": record.name}


@router.post("/__SEGMENT__/text", status_code=201, dependencies=OPEN)
async def text_body(request: Request, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("text")
    body = await request.body()
    record_id = await write_record(session, response)
    return {"id": record_id, "length": str(len(body))}


@router.post(
    "/__SEGMENT__/limited",
    status_code=201,
    dependencies=[
        Depends(public),
        Depends(idempotent),
        Depends(rate_limit(limit=1, window_seconds=2, key=probe_key)),
    ],
)
async def limited(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("limited")
    record_id = await write_record(session, response)
    return {"id": record_id, "name": record.name}


@router.post("/__SEGMENT__/mine", status_code=201, dependencies=[Depends(idempotent)])
async def mine(
    record: Record,
    caller: Annotated[Caller, Depends(signed_in)],
    session: Session,
    response: Response,
) -> dict[str, str]:
    CALLS.append("mine")
    record_id = await write_record(session, response)
    return {"id": record_id, "userId": str(caller.user_id)}


@router.post("/__SEGMENT__/plain", status_code=201, dependencies=[Depends(public)])
async def plain(record: Record, session: Session, response: Response) -> dict[str, str]:
    CALLS.append("plain")
    record_id = await write_record(session, response)
    return {"id": record_id, "name": record.name}
"""


class Probe:
    """The temporary module: its route paths, the handlers that ran, and its switches."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.segment = name.replace("_", "-")

    def path(self, route: str) -> str:
        return f"{MODULE_PREFIX}/{self.segment}/{route}"

    def _module(self) -> Any:
        return importlib.import_module(f"app.{self.name}")

    def calls(self) -> list[str]:
        return list(self._module().CALLS)

    def fail(self, route: str) -> None:
        self._module().FAILING.add(route)

    def recover(self, route: str) -> None:
        self._module().FAILING.discard(route)

    def started(self) -> Any:
        return self._module().STARTED

    def release(self) -> None:
        self._module().RELEASE.set()


def write_probe(module_folder: Any, table: str) -> Probe:
    """Create the probe module, whose routes write into `table`."""
    name = module_folder({"__init__.py": INIT_SOURCE})
    source = (
        API_SOURCE.replace("__TABLE__", table)
        .replace("__SEGMENT__", name.replace("_", "-"))
        .replace("__NAME__", name)
    )
    (APP_DIR / name / "api.py").write_text(source, encoding="utf-8")
    return Probe(name)


# -- Settings, callers, requests -----------------------------------------------------------


def use_services(set_settings: Any, monkeypatch: pytest.MonkeyPatch, **overrides: str) -> Any:
    """The real PostgreSQL and Redis, the general ceiling off; returns a key that signs tokens.

    The ceiling is off (`RATE_LIMIT_PER_MINUTE=0`, as for the earlier cards) so no test shares
    the counter of the in-process client's address.
    """
    private_key = new_private_key()
    values = {
        "DATABASE_URL": real_database_url(),
        "REDIS_URL": real_redis_url(),
        "RATE_LIMIT_PER_MINUTE": "0",
        "ACCESS_TOKEN_PUBLIC_KEYS": public_pem(private_key),
    }
    values.update(overrides)
    set_settings(**values)
    return private_key


def new_key() -> str:
    return str(uuid.uuid4())


def new_device() -> str:
    return str(uuid.uuid4())


def headers(
    device: str, key: str | None, *, token: str | None = None, content_type: str | None = None
) -> dict[str, str]:
    result = {DEVICE_HEADER: device}
    if key is not None:
        result[KEY_HEADER] = key
    if token is not None:
        result["Authorization"] = f"Bearer {token}"
    if content_type is not None:
        result["Content-Type"] = content_type
    return result


def signed_token(private_key: Any, user_id: str) -> str:
    return token_for(private_key, user_id)


def error_code(response: Any) -> str:
    body = response.json()
    assert set(body) == {"error"}, body
    error = body["error"]
    assert set(error) == ERROR_KEYS, error
    return str(error["code"])


def assert_status(response: Any, status: int, what: str) -> None:
    assert response.status_code == status, f"{what}: {response.status_code} {response.text}"


def assert_error(response: Any, status: int, code: str, what: str) -> None:
    assert_status(response, status, what)
    assert error_code(response) == code, f"{what}: {response.text}"


def assert_not_replayed(response: Any, what: str) -> None:
    assert REPLAYED_HEADER not in response.headers, (
        f"{what}: a response that ran the handler carries {REPLAYED_HEADER}"
    )


def assert_replay_of(replay: Any, first: Any, what: str) -> None:
    """`replay` is the stored response of `first`, marked `Idempotent-Replayed: true`."""
    assert replay.status_code == first.status_code, (
        f"{what}: status {replay.status_code}, the stored one is {first.status_code}: {replay.text}"
    )
    assert replay.json() == first.json(), f"{what}: {replay.text} is not {first.text}"
    assert replay.headers.get(REPLAYED_HEADER) == "true", (
        f"{what}: {REPLAYED_HEADER} is {replay.headers.get(REPLAYED_HEADER)!r}"
    )


# -- The database --------------------------------------------------------------------------


def migrate() -> None:
    """Bring the database of TEST_DATABASE_URL to the head migration (as in F3 and F8)."""
    result = run_alembic(real_database_url(), "upgrade", "head")
    assert result.returncode == 0, (
        f"exit code {result.returncode}\n--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}"
    )


async def create_probe_table() -> str:
    """A table of the test's own whose unique `marker` is checked at commit only."""
    name = f"f7probe_{uuid.uuid4().hex[:8]}"
    async with database_engine(real_database_url()) as engine, engine.begin() as connection:
        await connection.execute(
            text(
                f"CREATE TABLE {name} (marker text NOT NULL, "
                f"CONSTRAINT {name}_marker UNIQUE (marker) DEFERRABLE INITIALLY DEFERRED)"
            )
        )
    return name


async def drop_probe_table(name: str) -> None:
    async with database_engine(real_database_url()) as engine, engine.begin() as connection:
        await connection.execute(text(f"DROP TABLE IF EXISTS {name}"))


async def stored_markers(table: str) -> list[str]:
    async with database_engine(real_database_url()) as engine, engine.connect() as connection:
        # The table name is made by the test, never taken from input.
        rows = await connection.execute(text(f"SELECT marker FROM {table}"))  # noqa: S608
        return sorted(row[0] for row in rows)


_ROWS = (
    "SELECT key::text AS key, user_id::text AS user_id, device_id::text AS device_id, "
    "method, path, request_hash, status, response_headers, response_body, "
    "created_at, completed_at FROM idempotency_keys WHERE key::text = :key ORDER BY created_at"
)


async def key_rows(key: str) -> list[dict[str, Any]]:
    """The rows of `idempotency_keys` for `key`, of every owner."""
    async with database_engine(real_database_url()) as engine, engine.connect() as connection:
        rows = (await connection.execute(text(_ROWS), {"key": key})).mappings().all()
    return [dict(row) for row in rows]


async def run_sql(statement: str, **params: Any) -> None:
    async with database_engine(real_database_url()) as engine, engine.begin() as connection:
        await connection.execute(text(statement), params)


def as_json(value: Any) -> Any:
    """A stored body or header set as JSON, whether the column keeps JSON, text, or bytes."""
    if isinstance(value, bytes | bytearray | memoryview):
        value = bytes(value).decode("utf-8")
    if isinstance(value, str):
        return json.loads(value)
    return value
