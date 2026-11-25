# Test interface: F1 Backend skeleton and health check

What the F1 tests need from the code, and nothing more. The layout and
the behaviour come from the EPIC-11 spec, sections 2.1, 2.7, and 7.1
(cases F1-1 to F1-8).

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
