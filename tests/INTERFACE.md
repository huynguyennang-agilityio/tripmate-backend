# Test interface

What the tests need from the code, and nothing more, card by card:

- F1 Backend skeleton and health check: from the EPIC-11 spec, sections
  2.1, 2.7, and 7.1 (cases F1-1 to F1-8). Every section below up to
  "F3" is F1's.
- F3 Database session and migrations: from the EPIC-11 spec, sections
  2.2 (Transaction) and 4.2 (cases F3-1 to F3-5), in the sections whose
  names start with "F3".

## Running the tests

- The tests run from the repository root, in the project's own
  environment (`uv run pytest tests`), so `sys.executable` has every
  dependency of the app. They use `pytest`, `pytest-asyncio` (tests are
  marked `@pytest.mark.asyncio`), and `httpx`.
- `tests/` is a package (`tests/__init__.py`); shared helpers are in
  `tests/support.py`, fixtures in `tests/conftest.py`.
- The tests are not safe to run in parallel (F1-7 and F1-8 add a folder
  under `app/` for the length of one test).

### Environment of the test run

| Variable | Used by | Value |
|----------|---------|-------|
| `TEST_DATABASE_URL` | F1-1, F1-3, F1-6 | Address of a real PostgreSQL, in the form the app's `DATABASE_URL` takes, for example `postgresql+asyncpg://<user>:<password>@127.0.0.1:5432/<db>` |
| `TEST_REDIS_URL` | F1-1, F1-2, F1-6 | Address of a real Redis, for example `redis://127.0.0.1:6379/0` |

When either is missing, the test that needs it fails; it never skips.
The tests copy these into the app's own settings below; the app never
reads `TEST_*`.

A service that is down is a closed local port, for example
`postgresql+asyncpg://tripmate:<random>@127.0.0.1:<closed port>/tripmate`
and `redis://127.0.0.1:<closed port>/0`.

## Layout

```text
app/__init__.py
app/main.py         API process
app/worker.py       Worker process
app/scheduler.py    Scheduler process
app/platform/       Settings, health check, module scanning
app/<module>/       Found by scanning app/
```

## Settings

All settings come from environment variables only (no `.env` file is
read), read once at start-up and checked.

| Name | Required | Type | Meaning |
|------|----------|------|---------|
| `DATABASE_URL` | Yes | string | PostgreSQL address, `postgresql+asyncpg://...` |
| `REDIS_URL` | Yes | string | Redis address, `redis://...` |
| `RELEASE_VERSION` | Yes | string | The release version the health check returns |
| `PORT` | No | integer | The port the API process listens on |

- F1-4 and F1-5 hold for each of the three processes (API, worker, and
  scheduler); the `PORT` case of F1-5 is for the API only, the one process
  that listens on `PORT`.
- A missing required setting (F1-4): the process exits with a non-zero
  code before it reaches PostgreSQL or Redis, within 30 seconds, and its
  output (stdout or stderr) contains the setting's name. The output
  contains no setting's value: not the database address, not the
  password inside it, not the Redis address, not the release version,
  not the port.
- A setting with a value of the wrong type or the wrong shape (F1-5):
  the process exits with a non-zero code within 30 seconds, its output
  contains the setting's name, and it contains no part of the value
  (not the whole value, not the password, host, or port inside it).
  The cases:

  | Setting | Wrong value |
  |---------|-------------|
  | `PORT` | `not-a-number` |
  | `DATABASE_URL` | `postgresql://tripmate:<random password>.<random host>/tripmate` (the `@` is missing) |
  | `DATABASE_URL` | `postgresql+asyncpg://tripmate:<random password>@<random host>:<non-numeric port>/tripmate` |
  | `REDIS_URL` | `redis://<random host>:<non-numeric port>/0` |

## Processes

Each is started from the repository root with the project's Python, as
`<python> -m <module>`, with the settings above in its environment.

| Process | Command | Behaviour |
|---------|---------|-----------|
| API | `python -m app.main` | Checks the settings, then serves the app over HTTP on `PORT`, reachable at `127.0.0.1:<PORT>`, and keeps running |
| Worker | `python -m app.worker` | Checks the settings, then keeps running |
| Scheduler | `python -m app.scheduler` | Checks the settings, then keeps running |

F1-6 starts all three at once against the real PostgreSQL and Redis:
the API must answer HTTP on `PORT` within 30 seconds, and then all
three must still be running 5 seconds later. They are stopped with
SIGTERM.

## The app

```python
from app.main import create_app

app = create_app()   # a FastAPI app, built from the current environment
```

- `create_app()` reads the settings from the environment each time it is
  called, and scans `app/` for modules each time it is called, so a
  folder added after an earlier call is found by the next one.
- The app starts (its lifespan start-up and shut-down run without an
  error) when PostgreSQL or Redis does not answer.
- The tests call it in-process through `httpx.ASGITransport`, after
  running `app.router.lifespan_context(app)`.

## Health check

`GET /v1/health`, no headers needed.

| Given | Status | Body |
|-------|--------|------|
| PostgreSQL and Redis answer (F1-1) | `200` | JSON; one of its string values, at any depth, equals `RELEASE_VERSION` |
| PostgreSQL does not answer (F1-2) | `503` | `{"error": {"code": "server.unavailable", ...}}` (api-design.md, section 4.1) |
| Redis does not answer (F1-3) | `503` | `{"error": {"code": "server.unavailable", ...}}` |

## Modules found by scanning

- A module is a package directly under `app/`: a folder with an
  `__init__.py`, named like `f1probe_1a2b3c4d`.
- When the folder has an `api.py` (F1-7), the app includes the
  `router` it defines:

  ```python
  # app/<module>/api.py
  from fastapi import APIRouter

  router = APIRouter()

  @router.get("/<segment>")
  async def probe() -> dict[str, str]: ...
  ```

  The route is served at `/v1/<segment>`: `GET /v1/<segment>` returns
  the handler's JSON with `200`. The app's public OpenAPI document
  (`app.openapi()["paths"]`) lists `/v1/<segment>`, and no other path
  of it ends in `/<segment>`. An app built without the folder answers
  `GET /v1/<segment>` with `404`.
- The tests read the routes only through HTTP and the OpenAPI document,
  never through FastAPI's internal route list (`app.routes`).
- Adding the folder changes no other file: building the app and serving
  the route writes no file outside the folder, other than `__pycache__`
  and files git ignores.
- When the folder has no `api.py` (F1-8), the app still builds and
  starts, and its OpenAPI paths (`app.openapi()["paths"]`) are the same
  as without the folder.

## F3: Database session

`app/platform/db.py` holds the session (spec, section 2.1, `platform`
part `db`).

```python
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.platform.db import Base, get_session


@router.post("/<segment>")
async def handler(session: Annotated[AsyncSession, Depends(get_session)]) -> ...: ...
```

- `get_session` is a FastAPI dependency that gives the handler an
  SQLAlchemy `AsyncSession` on `DATABASE_URL`, in one database
  transaction for the request (spec, section 2.2, Transaction).
- The handler never commits. When the handler returns, the change is
  stored (F3-1); when it raises, nothing it wrote is stored (F3-2). The
  tests read the rows back through a connection of their own, after
  the response.
- The tests write with `session.execute(text("INSERT ..."))` into a
  table they create and drop themselves, from a temporary module's
  `api.py` (as in F1-7). A handler that raises a plain `RuntimeError`
  is an error the app does not handle; the tests do not check the
  response status, only that no row is stored.
- `Base` is the SQLAlchemy 2 declarative base (`DeclarativeBase`) of
  every model: a model's table is in `Base.metadata`.
- The platform's own models live in `app/platform/models.py`.
  `import_models()` loads them together with every module's
  `models.py`, so `Base.metadata` holds the platform's tables and the
  modules' tables alike.
- The request's session is used only during the request: nothing keeps
  it, or hands it to work that runs after the response (a background
  task, a job). Work after the request goes through the outbox (spec,
  section 2.5).

### F3: Review round 1

The tests below write into a table of their own whose unique `marker`
is `DEFERRABLE INITIALLY DEFERRED`, so a write that breaks it fails
only at commit. Each test first sends a control request that stores a
row, then reads the table back through its own connection. The app is
called with `raise_app_exceptions=False`, so an error the app does not
handle comes back as a `500` response.

- A module's error is `AppError` from `app/platform/errors.py` (spec,
  section 2.3), built with keyword arguments:

  ```python
  from app.platform.errors import AppError

  raise AppError(status=409, code="f3probe.conflict", params={}, fields=None)
  ```

  The response has the error's status. Its body is F4's and is not
  checked here.

| Finding | Given | Expected |
|---------|-------|----------|
| R1-4 (F3-2) | The handler writes a row, then raises `AppError` with status `409` | The response is `409`; no row is stored |
| R1-5 (F3-1) | The handler writes the same marker twice and returns, so the commit fails | The response is not `2xx`; no row is stored. The commit happens before the response. |
| R1-2 (F3-1) | The handler writes a row, calls `await session.commit()` itself, writes a second row, and returns | The response is not `2xx`; neither row is stored |
| R1-2 (F3-2) | The same, but the handler raises after the second write | The response is not `2xx`; neither row is stored |

The handler never commits: one transaction per request, committed by
the platform after the handler returns and before the response.

## F3: A clean database for each integration test

- Before each test under `tests/integration/`, the database of
  `TEST_DATABASE_URL` holds no row a previous test committed, in any
  table of its `public` schema, not only the tables of the models
  (F3-3). Alembic's own version table may keep its row.
- The cleaning happens without the test asking for it (an autouse
  fixture, for example in `tests/integration/conftest.py`), and it
  holds for rows committed through any connection: the app's and the
  test's own.
- Whether a table a test created is emptied or dropped is left open:
  F3-3 accepts both.
- F3-3 is two tests in one file, run in file order: the first creates
  a table, commits a row, and checks it is there; the second checks it
  finds no row of it. The second fails when run without the first.

## F3: Migrations

- Alembic is set up at the repository root (`alembic.ini`, with the
  migrations in `migrations/`), with its first migration, which is
  empty (spec, section 4.2, takes `idempotency_keys` and
  `outbox_events` to F7 and F8).
- The tests run it as `<python> -m alembic <command>` from the
  repository root, with the app's settings in the environment
  (`DATABASE_URL` is the database to migrate; `REDIS_URL` points at a
  closed port). Migrations read the database address from
  `DATABASE_URL`, never from `alembic.ini`.
- Each test runs against a new, empty database of its own on the
  PostgreSQL of `TEST_DATABASE_URL`, created and then dropped by the
  test (the user of `TEST_DATABASE_URL` needs the right to create
  databases).

| Case | Commands | Expected |
|------|----------|----------|
| F3-4 | `upgrade head`, then `downgrade base` | Both exit with `0`; after the upgrade `alembic_version` holds one revision, after the downgrade none |
| F3-5 | `upgrade head`, `check`; then, with a model that no migration creates, `check` again | `upgrade head` and the first `check` exit with `0`; the second `check` exits with a non-zero code and its output names the model's table |

- The migration check is `python -m alembic check`: it compares the
  models with the database at head (Alembic's autogenerate) and fails
  on any difference.
- Migrations compare against `Base.metadata` with every module's
  `models.py` loaded, found by scanning `app/` as for routes: F3-5's
  model is in `app/<temporary module>/models.py` (a module folder with
  `__init__.py` and `models.py`, no `api.py`), and adding it changes no
  shared file.
