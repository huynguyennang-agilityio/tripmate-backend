# Test interface

What the tests need from the code, and nothing more, card by card:

- F1 Backend skeleton and health check: from the EPIC-11 spec, sections
  2.1, 2.7, and 7.1 (cases F1-1 to F1-8). Every section below up to
  "F3" is F1's.
- F3 Database session and migrations: from the EPIC-11 spec, sections
  2.2 (Transaction) and 4.2 (cases F3-1 to F3-5), in the sections whose
  names start with "F3".
- F8 Outbox: from the EPIC-11 spec, sections 2.5 and 4.2, and
  data-model.md, section 6.2 (cases F8-1 to F8-5), in the section
  "F8: Outbox".
- F14 Flow checks and agent roles: from the EPIC-11 spec, section 2.12,
  development-flow.md, sections 2.1 and 2.3, and the task's scope
  (cases F14-1 to F14-11; F14-12 is a manual check), in the sections
  whose names start with "F14".
- F13 Requirement-to-test report: from the EPIC-11 spec, section 2.12
  (row "Requirement-to-test report"), test-strategy.md, sections 1 and
  5, and the task's scope, version 1.5 (cases F13-1 to F13-5), in the
  sections whose names start with "F13".
- F4 Error format and error codes: from the EPIC-11 spec, section 2.3,
  and api-design.md, sections 4.1 and 4.2 (cases F4-1 to F4-7), in the
  section "F4: Errors", with the product owner's decisions of
  2026-11-26 on the translation files and the pinned contracts version.
- F12 Contract publishing: from the EPIC-11 spec, section 2.10,
  api-design.md, sections 4.1 and 6, and the task's scope, version 1.7
  (cases F12-1 to F12-3; F12-4 to F12-6 are manual checks), in the
  section "F12: Contract publishing".
- F5 Access declaration and token check: from the EPIC-11 spec, section
  2.2 (rows "Device ID", "Access declaration", "Token check") and 2.3,
  security.md, section 2 (row "Access token"), and api-design.md,
  sections 2 and 4.2 (cases F5-1 to F5-10), in the section "F5: Access".
- F17 Examples on every success response: from the EPIC-11 spec,
  version 3.2, sections 2.10 and 2.7, and the task's scope, version 2.1
  (cases F17-1 to F17-4), in the section "F17: Examples on every
  success response".
- F6 Rate limits: from the EPIC-11 spec, version 2.8, sections 2.2 (row
  "Rate limits" and the order of the steps), 2.3, and the four F6
  decisions in 5.1, security.md, section 2.2, and api-design.md,
  section 4.2 (cases F6-1 to F6-6), in the section "F6: Rate limits".
- F16 Failing first sets up each test commit: from the EPIC-11 spec,
  section 2.12, and the task (cases F16-1 to F16-4), in the section
  "F16: Failing first sets up each test commit".
- F10 Logs, traces, metrics, and error reporting: from the EPIC-11 spec,
  version 3.3, sections 2.6 (with its row "Settings"), 2.7, 4.1, and the
  three F10 decisions in 5.1 (cases F10-1 to F10-6), in the section
  "F10: Observability".

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

## F8: Outbox

`app/platform/outbox.py` holds the outbox (spec, section 2.1,
`platform` part `outbox`). A module adds a domain event with one call
inside its transaction (spec, section 2.5):

```python
from app.platform.outbox import add_event

trip_seq: int = await add_event(
    session, trip_id=trip_id, type="expense.changed", payload={"id": "...", "version": 3}
)
```

- `session` is an SQLAlchemy `AsyncSession` inside an open
  transaction: the request's session from `get_session`, or a session
  the caller began (`async with AsyncSession(engine) as s, s.begin():`).
  The arguments after `session` are keyword-only; `trip_id` is a
  `uuid.UUID`, `payload` a JSON object. It returns the event's
  `trip_seq`.
- `add_event` never commits. The event is stored when that
  transaction commits and never when it rolls back.
- The table `outbox_events` is created by an Alembic migration
  (data-model.md, section 6.2, and the `OUTBOX_EVENTS` diagram):

  | Column | Type | Rule |
  |--------|------|------|
  | `id` | `bigint` | Primary key, from a sequence or identity |
  | `trip_id` | `uuid` | Not null; no foreign key yet (EPIC-02 adds it) |
  | `trip_seq` | `bigint` | Not null; unique together with `trip_id` |
  | `type` | `text` | Not null |
  | `payload` | `jsonb` | Not null |
  | `created_at` | `timestamptz` | Not null, set by the database |
  | `published_at` | `timestamptz` | Null; empty when the event is added |

- `trip_seq` numbering, per trip: the first event of a trip gets `1`,
  each next event the previous plus one, with no gap, also across
  transactions, also when a transaction that added an event rolled
  back (its number is used again), and also when two transactions of
  the same trip run at the same time (the second waits for the first;
  both commit; their values differ and are consecutive). Within a
  transaction, `id` order follows the order the events were added.
- How the code keeps the per-trip counter is not part of the
  interface (it may use a helper table); the tests read only
  `outbox_events` and their own probe table.
- The tests bring the database of `TEST_DATABASE_URL` to the head
  migration themselves, once per test file: `<python> -m alembic
  upgrade head` from the repository root, as in "F3: Migrations".
  Without `TEST_DATABASE_URL` the tests fail; they never skip.
- The tests read the rows back through a connection of their own,
  after the commit. Before each test the autouse cleaning empties
  every table ("F3: A clean database for each integration test").

| Case | Given | Expected |
|------|-------|----------|
| F8-1 | A transaction adds one event and commits (on a new trip; and after an earlier committed event of the trip) | One row with the trip's next `trip_seq` (`1`, then `2`), the `type` and `payload` given, and `published_at` empty |
| F8-2 | A request whose handler (`Depends(get_session)`) writes a record into the probe table, adds an event, then raises; and the same in a session the test began | No outbox row and no record. A control request of the same shape that returns stores both. After the rollback, the trip's next committed event gets `1` |
| F8-3 | One transaction adds two events | Two rows with consecutive `trip_seq` (`1`, `2`), in `id` order matching the order added |
| F8-4 | Two sessions of the same trip: the second adds its event while the first's transaction is still open; both commit | Their `trip_seq` values differ and are `1` and `2`; a step that waits more than 15 seconds fails the test |
| F8-5 | Events of two trips, interleaved within and across transactions | Each trip is numbered `1`, `2`, ... on its own |

## F14: Flow checks

The checks are a typed Python package `flowchecks/` at the repository
root (not under `app/`), one module per check. Each module has
importable functions (below) and a command line,
`python -m flowchecks.<name> ...`, run from the repository root, that
exits with `0` when the check passes and with a non-zero code when it
fails, and then prints (stdout or stderr) each thing that failed: the
test IDs, the paths, or the mutants. The functions return that same
list; an empty list is a pass.

The checks are generic: the other code repositories call them with
their own path list and test command, so nothing in them names
`tripmate-backend`.

The tests are in `tests/unit/flow/test_F14_*.py`, with their helpers in
`tests/unit/flow/repo.py`.

### F14: Commits under check

- `repo` is the path of a git repository; `base` and `head` are git
  revisions (a sha or a branch). The commits under check are those of
  `base..head`.
- A test commit is one whose message has the trailer `Agent: test`
  (git's trailer syntax: the last paragraph of the message). Every other
  commit is not a test commit.
- A test is identified by its ID, `<path>::<function>`, where `<path>`
  is the test file's path from the repository root, with `/`, for
  example `tests/test_calc.py::test_adds_two_numbers`. That is pytest's
  node ID for a module-level test function. The tests use only
  module-level `def test_*` functions in files named `test_*.py`.
- The tests build throwaway repositories under `tmp_path` with `git`
  (local `user.name` and `user.email`), with a base commit holding
  `calc.py`, `pytest.ini` (`pythonpath = .`), `.gitignore`
  (`__pycache__/`, `.pytest_cache/`, `.hypothesis/`), and a test that
  already passes, `tests/test_old.py`.

### F14: Failing first (`flowchecks.failing_first`)

```python
from flowchecks import failing_first

passing: list[str] = failing_first.check(repo, base, head, test_command)
```

- `repo`: `str | os.PathLike[str]`; `base`, `head`: `str`;
  `test_command`: `Sequence[str]`, the command that runs tests, for
  example `(sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")`.
- The tests a commit adds are the test IDs present after the test
  commits and absent at `base`. The check checks out the last test
  commit of `base..head` and runs each added test as
  `test_command + [test ID]`, from the repository root of that
  checkout. A non-zero exit is a failure or an error, as the rule asks;
  `0` is a pass.
- It returns the IDs of the added tests that passed (F14-2), in any
  order, and `[]` when every added test failed or errored (F14-1) or
  when `base..head` has no test commit (F14-5). Tests already at `base`
  are not run.
- Where the checkout happens (the repository itself or a separate
  worktree) is left to the code; the tests' repositories are thrown
  away after each test.
- Command line:
  `python -m flowchecks.failing_first --repo <dir> --base <rev> --head <rev> --test-command "<command>"`,
  where `<command>` is split as a shell would (`shlex.split`).

| Case | Given | Expected |
|------|-------|----------|
| F14-1 | The test commit adds one test that fails (`add` raises) and one whose module fails to import (`multiply` is missing); a later code commit makes both pass at the head | `check` returns `[]`; the command exits `0` |
| F14-2 | The test commit adds a failing test and `tests/test_trivial.py::test_already_passes`, which passes at that commit | `check` returns a list that holds that ID; the command exits non-zero and prints it |
| F14-5 | One commit with no trailer that changes `README.md` and adds `docs/guide.md` | `check` returns `[]`; the command exits `0` |

### F14: Test lock (`flowchecks.test_lock`)

```python
from flowchecks import test_lock

broken: list[str] = test_lock.check(repo, base, head)
```

- The locked tests are those the test commits of `base..head` added
  (absent at `base`). A locked test is broken when a later commit that
  is not a test commit changes its function (its body or its
  decorators), skips it, or deletes it, or deletes its file. A later
  test commit may change it.
- A test is skipped when a later commit adds a skip marker to it: on
  the function (`@pytest.mark.skip(...)`) or for the whole file
  (`pytestmark = pytest.mark.skip(...)`).
- It returns the IDs of the broken locked tests, in any order, and
  `[]` when none is broken or when there is no test commit.
- Command line:
  `python -m flowchecks.test_lock --repo <dir> --base <rev> --head <rev>`.

| Case | Given | Expected |
|------|-------|----------|
| F14-3 | Test commit adds `tests/test_calc.py` with `test_adds_two_numbers` and `test_adds_zero`; a code commit; then a commit with no trailer that (each in turn) edits the first test's assertion, adds `@pytest.mark.skip` to it, adds a module-level `pytestmark` skip, deletes the function, or deletes the file | `check` returns a list holding `tests/test_calc.py::test_adds_two_numbers`; the command exits non-zero and prints it |
| F14-4 | The same, but the edit is in a commit with the trailer, followed by a code commit that leaves the test alone | `check` returns `[]`; the command exits `0` |
| F14-5 | As for failing first | `check` returns `[]`; the command exits `0` |

### F14: The path list

- The backend's path list is `.github/risk-paths.toml`. Each repository
  that calls the checks has its own file and passes its path.
- It is TOML: an array of tables `rule`, each with `glob` (string) and
  `level` (`"High"`, `"Medium"`, or `"Low"`, the levels of
  development-flow.md, section 2.1), and an optional `reason`
  (string) the checks do not read:

  ```toml
  [[rule]]
  glob = "migrations/**"
  level = "High"
  reason = "A migration or a table"

  [[rule]]
  glob = "app/money/splits.py"
  level = "Medium"
  reason = "Rule file: splits.allocate"
  ```

- A glob is matched against the path from the repository root, with
  `/`, as `pathlib.PurePosixPath(path).full_match(glob)` does: `*` stays
  within one folder, `**` matches any number of folders, none included
  (`**/CLAUDE.md` matches `CLAUDE.md` and `app/trips/CLAUDE.md`).
- A path's level is the highest level of the rules that match it. A
  path no rule matches is `"High"` (F14-10).
- A `"Medium"` rule names a rule file: the pure-function files of
  architecture.md, section 4 ("Where each domain rule lives"), for
  example `app/money/splits.py`. A path is a rule file when any
  `"Medium"` rule matches it, whatever other rules also match it.
- The backend's file holds the High items of development-flow.md,
  section 2.1, and also names, as High: itself
  (`.github/risk-paths.toml`), the workflows (`.github/workflows/**`),
  every `CLAUDE.md`, and the agent roles (`.claude/agents/**`) (F14-9).
  The tests also rely on it giving `migrations/versions/<file>.py` the
  level High (F14-7) and `app/trips/service.py` a level below High
  (F14-6, one module), and on `app/money/splits.py` being a rule file
  (F14-11).

### F14: Risk gate (`flowchecks.risk_gate`)

```python
from flowchecks import risk_gate

rules = risk_gate.load_rules(path)                   # the path list above
level: str = risk_gate.level_of("CLAUDE.md", rules)  # "High", "Medium" or "Low"
needing: list[str] = risk_gate.check(changed, rules, labels)
```

- `load_rules(path: str | os.PathLike[str])` reads a path list file;
  the tests pass its result back to the other functions without looking
  inside it.
- `check(changed: Iterable[str], rules, labels: Iterable[str])`:
  `changed` are the changed paths from the repository root; `labels`
  the pull request's labels. When `human-approved` is among the labels
  it returns `[]`; otherwise it returns the changed paths whose level is
  `"High"`, in any order, and no other path.
- Command line:
  `python -m flowchecks.risk_gate --rules <file> --repo <dir> --base <rev> --head <rev> [--label <label> ...]`.
  The changed paths are the files that differ between `base` and `head`
  (`git diff --name-only <base>...<head>`).

| Case | Path list | Changed | Labels | Expected |
|------|-----------|---------|--------|----------|
| F14-6 | Own: `migrations/**`, `app/platform/**` High; `app/trips/**`, `tests/unit/trips/**`, `README.md` Low | `app/trips/service.py`, `app/trips/api.py`, `tests/unit/trips/test_trips.py` | none | `[]`; command exits `0` |
| F14-6 | Backend | `app/trips/service.py` | none | `[]` |
| F14-7 | Own | `migrations/versions/0002_add_trips.py`, `app/trips/service.py` | none | holds the migration, not the service file; command exits non-zero and prints the migration |
| F14-7 | Backend | the migration | none | holds the migration |
| F14-8 | Own | as F14-7 | `human-approved` | `[]`; command exits `0` |
| F14-9 | Backend | each of `.github/risk-paths.toml`, `.github/workflows/ci.yml`, `.github/workflows/flow-checks.yml`, `CLAUDE.md`, `app/trips/CLAUDE.md`, `.claude/agents/test-agent.md`, `.claude/agents/code-agent.md` | none, then `human-approved` | `level_of` is `"High"`; holds the path; with the label `[]` |
| F14-10 | Own | `scripts/new_tool.sh` (no rule), `README.md` | none, then `human-approved` | `level_of` is `"High"`; holds it; with the label `[]`; command exits non-zero and prints it |

### F14: Mutation (`flowchecks.mutation`)

```python
from flowchecks import mutation

shown: list[str] = mutation.check(changed, rules, pr_description, run_mutants)
```

- `changed`: the changed paths; `rules`: from `risk_gate.load_rules`;
  `pr_description`: the pull request description (Markdown text);
  `run_mutants: Callable[[Sequence[str]], Iterable[str]]`.
- The check finds the changed rule files (the path list's `"Medium"`
  rules) and calls `run_mutants` once with them (paths from the
  repository root, in any order). `run_mutants` runs mutation testing on
  those files and returns the names of the surviving mutants. The tests
  give it a stand-in that returns mutmut 3 style names such as
  `app.money.splits.x_allocate__mutmut_3`; the command line uses a
  runner that runs mutmut and reads its results.
- A surviving mutant is listed when the description has a
  `## Surviving mutants` section (from that heading to the next `## `
  heading or the end) with a line `- <mutant name>: <reason>`, the
  reason not empty (task scope, version 1.2).
- It returns the surviving mutants that are not listed, in any order;
  `[]` when every survivor is listed.
- Command line (not run by the tests, since it runs mutmut):
  `python -m flowchecks.mutation --rules <file> --repo <dir> --base <rev> --head <rev> --description-file <file>`,
  which prints each mutant it returns.

| Case | Changed | Survivors | Description | Expected |
|------|---------|-----------|-------------|----------|
| F14-11 | `app/money/splits.py` (own list: Medium), `README.md` | `app.money.splits.x_allocate__mutmut_3` | No `## Surviving mutants` section; the section lists only another mutant; the line names it with no reason; it is listed with a reason only under `## Notes` | Holds the mutant; `run_mutants` called once with `["app/money/splits.py"]` |
| F14-11 | The same | The same | The section lists it with a reason | `[]` |
| F14-11 | The same, with the backend's path list | The same | No section | Holds the mutant; `run_mutants` called with `["app/money/splits.py"]` |

## F15: Failing first in any language

F15 widens `flowchecks.failing_first` (section "F14: Failing first")
to the test files of any language (EPIC-11 spec, section 2.12). The
tests are in `tests/unit/flow/test_F15_failing_first_any_language.py`,
with the helpers of `tests/unit/flow/repo.py`.

```python
from flowchecks import failing_first

passing: list[str] = failing_first.check(
    repo,
    base,
    head,
    test_command,
    test_file_pattern="tests/**/*.test.js",
    no_test_pattern="^# tests 0$",
)
```

- `test_file_pattern`: `str`, keyword-only, the caller's test file
  pattern. Its default is `"test_*.py"`, so the backend's calls of F14
  (without it) are unchanged.
- Matching: a pattern without `/` is matched against the file name (as
  `test_*.py` matches `tests/unit/test_calc.py`); a pattern with `/` is
  matched against the path from the repository root, where `**`
  matches any number of folders. The tests only use paths on which
  every reading of `**` agrees (`tests/unit/calc.test.js` for
  `tests/**/*.test.js`).
- A file that matches the pattern and ends in `.py` is a Python test
  file: it keeps the per-function check of F14 (test IDs
  `<path>::<function>`, pytest's exit codes).
- Any other file that matches the pattern is checked per file. Its ID
  is its path, for example `tests/unit/calc.test.js`. A file is added by
  a test commit when it is absent at that commit's parent; it is run at
  that commit as `test_command + [path]`, from the repository root of
  the checkout. Any non-zero exit other than `126` and `127` is a
  failure, as the rule asks, unless the output matches the no-test
  pattern (below); `0` is a pass, and `check` returns the path.
- A file that exists at the parent and that the test commit only
  changes is not run and not asked to fail (F15-3).
- `no_test_pattern`: `str | None`, keyword-only, default `None`: the
  caller's regular expression that says a run found no test in the
  file, for example `^# tests 0$` for `node --test`. It is matched with
  `re.search` and `re.MULTILINE` (so `^` and `$` are the start and end
  of each line) against the run's output, stdout and stderr. `None`
  (or an empty string on the command line or in the workflow) means no
  no-test detection: the run is judged by its exit code alone. It
  applies only to files of another language; a Python test file keeps
  F14's check.
- An error of the run, for a file of another language, is one of:
  - the test command cannot start: it is not found or cannot be run
    (for example a file with no execute permission), so no exit code
    exists;
  - the run exits `126` or `127` (a shell's codes for a command that
    cannot be run or is not found);
  - the run's output matches `no_test_pattern`, whatever the exit code.

  On an error of the run `check` raises `failing_first.RunError`,
  whose message names the file. It is never counted as a failure of
  the test, so the check never passes on it.
- Command line: the F14 command with
  `--test-file-pattern "<pattern>"` (default `test_*.py`) and
  `--no-test-pattern "<regex>"` (default none). It exits
  non-zero and prints the path of each passing file. On an error of the
  run it exits non-zero and prints the file and the words
  `error of the run`, as F14 already does.
- The reusable workflow takes the pattern as the input
  `test-file-pattern` (default `test_*.py`) and the no-test pattern as
  the input `no-test-pattern` (default empty: no no-test detection),
  and passes them to the command line. Its set-up of the caller's
  runtime (for example Node 24) is shown on one CI run of F15's pull
  request; no unit test reads the workflow.
- The JavaScript tests run with Node's own runner, `node --test <file>`
  (`node` on the PATH; Node 24 locally, the runner's Node in CI). A
  missing `node` fails these tests; they never skip.
- F15-5 also runs small Python runners written under `tmp_path`,
  outside the repository under check, as the test command: one that
  exits with a code it is given (`126`, `127`), and one that prints a
  TAP summary holding the line `# tests 0` and exits `0`. The test
  command for the not-executable case is a file under `tmp_path`
  without execute permission.

| Case | Given | Expected |
|------|-------|----------|
| F15-1 | A test commit adds `tests/unit/calc.test.js`, which passes at that commit; a later code commit | `check` returns a list that holds `tests/unit/calc.test.js`; the command exits non-zero and prints it |
| F15-2 | A test commit adds `tests/unit/calc.test.js`, which fails at that commit (`add` throws); a later code commit makes it pass | `check` returns `[]`; the command exits `0` |
| F15-3 | `tests/unit/old.test.js` is at `base`; a test commit only changes it, and it still passes | `check` returns `[]`; the command exits `0` |
| F15-4 | With the pattern `test_*.py`, a test commit adds `tests/test_calc.py` with one failing and one passing function | `check` returns exactly `["tests/test_calc.py::test_already_passes"]`; the command exits non-zero and prints that ID and not the failing one |
| F15-5 | A test commit adds `tests/unit/calc.test.js`; the test command is a program no machine has | `check` raises `RunError` naming the file; the command exits non-zero and prints the file and `error of the run` |
| F15-5 | The same, the test command a file without execute permission | The same |
| F15-5 | The same (the file fails under Node), the test command a runner that exits `126`, then one that exits `127` | The same, for each |
| F15-5 | A test commit adds `tests/unit/calc.test.js` holding no test; the runner prints `# tests 0` and exits `0`; `no_test_pattern="^# tests 0$"` | The same |
| F15-5 (control) | A test commit adds the failing `tests/unit/calc.test.js`; `node --test` with `no_test_pattern="^# tests 0$"` (Node prints `# tests 1`) | `check` returns `[]`; the command exits `0` |

## F16: Failing first sets up each test commit

F16 adds the caller's setup command to `flowchecks.failing_first`
(sections "F14: Failing first" and "F15: Failing first in any
language"; EPIC-11 spec, section 2.12; tasks/EPIC-11.md, F16). The
tests are in `tests/unit/flow/test_F16_setup_each_test_commit.py`,
with the helpers of `tests/unit/flow/repo.py`.

```python
from flowchecks import failing_first

passing: list[str] = failing_first.check(
    repo,
    base,
    head,
    test_command,
    test_file_pattern="tests/**/*.test.js",
    setup_command="npm ci",
)
```

- `setup_command`: `str | None`, keyword-only, default `None`: the
  caller's setup command, one string run through a shell as
  `bash -c "<setup_command>"`, the same way the reusable workflow runs
  its input `setup-command`. So it may hold `&&`, `;`, quotes,
  redirections, and `$PWD`. It inherits the check's environment.
- Each test commit that adds test files gets its own git worktree, a
  fresh checkout of that commit (detached, so `git rev-parse HEAD` in
  it gives that commit's full SHA), never the caller's repository
  itself. Nothing untracked carries over from one test commit's
  worktree to the next: a file that the setup command (or a test)
  wrote in the worktree of one test commit is absent in the worktree
  of the next (F16-4).
- In each such worktree the setup command runs exactly once, with the
  worktree's root as its current directory, before any test file that
  commit adds is run there. The test command then runs from that same
  root, as in F14 and F15, so it may be a path the setup wrote, for
  example `node_modules/.bin/vitest` (F16-1).
- The setup command runs under the check's `timeout`, like each test
  run.
- An error of the run, from the setup command, is one of: it exits
  non-zero (including `bash`'s `127` for a command it cannot find), it
  cannot start, or it runs past the timeout. `check` then raises
  `failing_first.RunError`, whose message names the full SHA of the
  test commit whose setup broke (and not the SHA of another test
  commit). It is never counted as a failure of a test, so the check
  never passes on it.
  - The message also holds the setup command's output, stdout and
    stderr; on a timeout it holds the words `timed out`.
  - None of the test files that commit adds is run once its setup has
    broken.
- The setup command runs once per test commit, however many test files
  or test functions that commit adds (F16-4).
- After the check, whether it passed, failed, or raised `RunError`,
  the caller's repository has no worktree left:
  `git worktree list --porcelain` shows only the main tree.
- `None` or `""` means no setup command: failing first runs as in F15
  (F16-3), with the same results.
- Command line: the F15 command with `--setup-command "<command>"`,
  the string given to `bash -c` as is (not split). Its default is
  empty, and an empty value means no setup command, as the workflow's
  default empty input does. On an error of the setup command it exits
  non-zero and prints the commit's SHA and the words
  `error of the run`, as F15 does for a file.
- The reusable workflow passes its input `setup-command` to the command
  line. No unit test reads the workflow.
- The tests run the JavaScript test files with `node --test`, as in
  F15 (`node` on the PATH; a missing `node` fails them). F16-4's setup
  command appends a line to a log file under `tmp_path`, outside the
  repository under check, so the test can read which commit and which
  directory each run had.

| Case | Given | Expected |
|------|-------|----------|
| F16-1 | The test command is `node_modules/.bin/runner`, a script that runs `node --test` and that only the setup command writes (it is not committed); a test commit adds `tests/unit/calc.test.js`, which fails at that commit (`add` throws) | `check` returns `[]` and the caller's repository has no `node_modules`; the command with `--setup-command` exits `0` |
| F16-2 | Two test commits; the setup command `sh tests/setup.sh` exits `0` at the first and `4` at the second | `check` raises `RunError` naming the second commit's SHA and not the first's; the command exits non-zero and prints that SHA and `error of the run` |
| F16-2 | The setup command is a program no machine has | The same, naming the test commit's SHA |
| F16-2 | The setup command `sleep 30` with `timeout=2` (`--timeout 2`); the test command a runner outside the repository that logs each file it runs | `RunError` names the commit's SHA and says `timed out`; the runner's log is never written; no worktree is left; the command exits non-zero and prints the same and `error of the run` |
| F16-2 | The setup command `echo F16-SETUP-OUT; echo F16-SETUP-ERR >&2; exit 3`, the same logging runner | `RunError` names the commit's SHA and holds both lines of output; the runner's log is never written; no worktree is left; the command prints the same |
| F16-3 | No setup command (`setup_command=None`, `""`, or left out; on the command line `--setup-command ""` or left out); a test commit adds one passing and one failing JavaScript test file | `check` returns exactly the passing file's path, as in F15; the command exits non-zero and prints it |
| F16-4 | Two test commits, each adding a failing test file; the setup command logs `git rev-parse HEAD`, `$PWD`, and whether a marker file exists, then writes the marker | The log has two lines, one per test commit SHA, with two distinct directories, neither the caller's repository; the marker is absent at both runs; `check` returns `[]` |
| F16-4 | One test commit adds two failing test files; the logging setup and the logging runner | The setup log has exactly one line, for that commit; the runner logged both files at that commit, each after the setup (its marker present); `check` returns `[]`, the command exits `0`, and no worktree is left |

## F13: Requirement-to-test report (`flowchecks.requirements`)

The check is a module of the `flowchecks/` package, with the same shape
as the F14 checks: importable functions and a command line,
`python -m flowchecks.requirements ...`, run from the repository root.
It reads only the files it is given (the ID file and the JUnit XML
files) and writes only into `--out` and `$GITHUB_STEP_SUMMARY`; it reads
nothing outside the repository (not prd.md). In CI it is the job
`requirements`, which runs after `unit` and `integration` and reads the
JUnit XML each of them writes (`pytest --junitxml=<file>`).

The tests are in `tests/unit/flow/test_F13_requirements.py`. They build
the ID file and the JUnit XML under `tmp_path` and never read the
repository's own test results.

### F13: The ID file (`tests/requirement-ids.txt`)

- UTF-8 text, one requirement ID per line, in the form of prd.md:
  capital letters, a hyphen, two digits (`AUTH-01`, `EXP-04`).
- Text from `#` to the end of the line is a comment; surrounding spaces
  are ignored; a line left empty is ignored.
- The IDs listed are the P0 IDs in scope. An ID not listed is not asked
  for (F13-3). The first card of each epic adds that epic's IDs;
  EPIC-11 has none, so the repository's file holds only its header
  comment.
- A line that is not an ID after comments are removed (for example
  `auth_01`) is an error of the file: the command exits with `2` and
  names the line's number (as `line 3` or `<file>:3`; lines count from
  1, comment and empty lines included) (F13-3).

### F13: Tests read from JUnit XML

- A JUnit XML file is what pytest's `--junitxml` writes: a root
  `<testsuites>` with `<testsuite>` elements holding `<testcase
  classname="..." name="...">`. The check reads one or more such files
  and takes every `<testcase>` of all of them.
- A test's label is `<classname>::<name>`, for example
  `tests.integration.money.test_splits::test_EXP_04_remainder_goes_by_join_order`.
- A testcase with a `<skipped>` child is not a test (F13-5): it counts
  for no ID and is not in the report's lists of tests. (pytest also
  writes an expected failure, `xfail`, as `<skipped>`.) One exception:
  a skipped testcase named with an ID that is not in the file is still
  an unknown test (F13-4).
- A testcase with a `<failure>` or `<error>` child is still a test of
  its ID: it ran, and the `unit` or `integration` job already fails on
  it (F13-5).
- The requirement ID of a test is read from `name` (the function name,
  with any `[parameters]` suffix): `test_`, then capital letters, an
  underscore, two digits, then `_`, `[`, or the end of the name.
  `test_AUTH_01_password_is_hashed` gives `AUTH-01`. Only an ID right
  after `test_` is read. `test_AUTH_01[param-a]`, a bare `test_AUTH_01`,
  and a method of a test class (`classname` ending in `.TestThing`) give
  `AUTH-01`; `test_login_AUTH_01_x`, `test_step_01_x`, `test_AUTH_1_x`,
  and `test_AUTH_012_x` give no ID: they count for no ID and are not
  unknown (F13-1). A card case ID (`test_F1_4_...`,
  `test_CT1_2_...`, `test_F13_2_...`) and a test with no ID give no ID
  and are not checked (F13-1, F13-3).

### F13: The check

```python
from flowchecks import requirements

ids: list[str] = requirements.load_ids(ids_file)       # in file order
report = requirements.check(ids_file, junit_files)
report.tests_by_id   # Mapping[str, Sequence[str]]
report.missing       # Sequence[str]
report.unknown       # Sequence[str]
```

- `ids_file`: `str | os.PathLike[str]`; `junit_files`:
  `Iterable[str | os.PathLike[str]]`.
- `tests_by_id` has one key for every ID of the ID file, and no other
  key; its value is the labels of the tests of that ID (skipped ones
  left out), in any order, or empty.
- `missing`: the IDs of the file with no test (an empty
  `tests_by_id` entry), in any order (F13-2, F13-5).
- `unknown`: the labels of the tests named with an ID that is not in the
  file, skipped ones included, in any order (F13-4).
- The check passes when `missing` and `unknown` are both empty.

### F13: Command line and report files

`python -m flowchecks.requirements --ids <file> --junit <file> [--junit <file> ...] --out <dir>`

- Writes `<dir>/requirements-report.md` and
  `<dir>/requirements-report.json` (creating `<dir>` when needed), on a
  pass and on a failure alike, so the report is kept with every run.
- When `GITHUB_STEP_SUMMARY` is set, appends the Markdown report to that
  file (the job summary on the run page), on a pass and on a failure
  alike; text already in the file is kept as it was (F13-2, F13-4).
- Exits `0` when the check passes; otherwise exits non-zero and prints
  (stdout or stderr) each missing ID and each unknown test's label.
- `requirements-report.json`:

  ```json
  {
    "ids": {"AUTH-01": ["<label>", "..."], "EXP-04": []},
    "missing": ["EXP-04"],
    "unknown": ["<label>"]
  }
  ```

  The same content as the result of `check`; lists in any order.
- `requirements-report.md`: every ID of the file with the labels of its
  tests (the layout, for example a table, is the code's), the missing
  IDs, and the unknown tests. Tests that give no ID are not in it.

| Case | ID file | Tests (JUnit) | Expected |
|------|---------|---------------|----------|
| F13-1 | Comments, blank lines, `AUTH-01`, `EXP-04  # ...` | Unit: one `test_AUTH_01_...`, plus `test_F1_4_...`, `test_CT1_2_...`, `test_F13_2_...`, a test with no ID; integration: one `test_AUTH_01_...`, one `test_EXP_04_...` | `load_ids` gives `["AUTH-01", "EXP-04"]`; `tests_by_id` gives both IDs their tests; `missing`, `unknown` empty; command exits `0`; JSON as the result; Markdown names each ID and test and none of the tests without an ID; the job summary holds the Markdown |
| F13-2 | `AUTH-01`, `AUTH-02` | One `test_AUTH_01_...` | `missing == ["AUTH-02"]`; command exits non-zero and prints `AUTH-02`; the report files are written and name it; the job summary keeps its earlier text and the appended report names it |
| F13-3 | `AUTH-01` | `test_AUTH_01_...`, `test_F1_4_...`; no `EXP-04` test | Keys only `AUTH-01`; nothing missing or unknown; exits `0`; `EXP-04` is nowhere in the output or the report. The repository's own `tests/requirement-ids.txt` loads with `load_ids`. An ID file whose line 3 is `auth_01`: exits `2` and names line 3 |
| F13-4 | `AUTH-01` | `test_AUTH_01_...` (unit), `test_EXP_04_...` and a skipped `test_EXP_05_...` (integration) | `unknown` is the labels of both; exits non-zero and prints both; the job summary keeps its earlier text and the appended report names both |
| F13-5 | `AUTH-01`, `AUTH-02`, `AUTH-03` | `AUTH_01` only skipped; `AUTH_02` one skipped, one failed; `AUTH_03` one errored | `missing == ["AUTH-01"]`; `AUTH-02` has the failed test, `AUTH-03` the errored one; exits non-zero and prints `AUTH-01` |

### F13: The `requirements` CI job

`.github/workflows/ci.yml`, read as YAML by
`test_F13_1_ci_keeps_the_report_with_each_run`:

- The jobs `unit` and `integration` each run pytest with
  `--junitxml=<file>` and upload that file as an artifact
  (`actions/upload-artifact`) in a step with `if: ${{ !cancelled() }}`.
- The job `requirements` has `needs` holding `unit` and `integration`
  and the job-level `if: ${{ !cancelled() }}`: it runs when a test job
  failed, but not when the run was cancelled.
- It runs `python -m flowchecks.requirements` with
  `--ids tests/requirement-ids.txt`, two `--junit <file>`, and
  `--out <dir>`, and uploads `<dir>` as the artifact
  `requirements-report` in a step with `if: ${{ !cancelled() }}`.

## F4: Errors

The tests are in `tests/api/platform/test_F4_errors.py` (F4-1 to F4-5,
level A) and `tests/unit/flow/test_F4_error_code_texts.py` (F4-6 and
F4-7, level C).

### F4: Raising an error

A temporary module under `app/` (as in F1-7, through the
`module_folder` fixture) serves routes from its `api.py`, and the tests
call the app in-process (`client_for(build_app())`) with the settings of
`set_settings()` (PostgreSQL and Redis on closed ports; no route of the
tests uses them).

```python
from app.platform.errors import AppError

raise AppError(status=410, code="f4probe.expired",
               params={"expiredAt": "2026-12-01T10:00:00Z"}, fields=None)
```

- A body schema is a Pydantic model taken as the handler's body
  parameter (`async def create_item(body: ItemBody)`), validated by
  FastAPI as usual; the platform turns the validation failure into
  `400 request.invalid`.
- Something unexpected is a plain `RuntimeError`; for it the app is
  called with `raise_app_exceptions=False`, so the error comes back as
  the app's response.
- Every request the tests send carries a valid `X-Device-Id` (a UUID),
  so they stay valid when F5 requires it.

### F4: The response

Every error response's body is exactly `{"error": {...}}`, and the
`error` object has exactly the keys `code`, `params`, `fields`, and
`requestId` (api-design.md, section 4.1). `requestId` is a non-empty
string, equal to the response header `X-Request-Id`.

| Case | Given | Expected |
|------|-------|----------|
| F4-1 | The handler raises the `AppError` above | `410`; `code` `f4probe.expired`; `params` as raised; `fields` `null` |
| F4-2 | `POST` with `{"amount": "not-a-number"}` to a body `{name: str, amount: int}` | `400`; `code` `request.invalid`; `fields` is a list of two entries, each exactly `{"field", "code"}`; the `field` values are `amount` and `name` (a top-level body field is named as in the schema, with no `body.` prefix); each entry's `code` is a string of the form `<area>.<reason>` in snake case (which code is the code's choice) |
| F4-3 | `POST` with `Content-Type: application/json` and a body that is not JSON | `400`; `code` `request.invalid` (`params` and `fields` not checked) |
| F4-4 | The handler raises `RuntimeError(<random secret>)` | `500`; `code` `server.error`; `params` `{}` or `null`; `fields` `null`; the body holds neither the secret, nor `RuntimeError`, `Traceback`, or `File "` |
| F4-5 | Two successful requests, and two requests that fail with `AppError` | Each successful response carries `X-Request-Id`; the two values differ; the two `requestId` values differ |

### F4: Registering codes

A module registers the codes it can return in its `register(registry)`
hook (`app/platform/modules.py`):

```python
# app/<module>/__init__.py
def register(registry):
    registry.add_error_codes("f4probe.expired")
```

F4-6 reads the registered codes with
`from app.platform.modules import scan_modules; scan_modules().error_codes`;
the set holds every code a temporary module registered.

### F4: The error code text check (`flowchecks.error_codes`)

The texts live in `tripmate-contracts`, at the version the backend pins
by commit SHA; CI checks that repository out at the SHA. The check
itself reads only a folder on disk and the set of codes it is given; it
never uses the network.

- The folder is `translations/`, holding one folder per language,
  `en` and `vi`. Each holds `<area>.json` files, each one flat JSON
  object of strings, UTF-8. The key is the error code; the area is the
  part of the code before the first dot (`request.invalid` is a key of
  `translations/vi/request.json`). A file may hold keys of codes that
  are not registered.
- A code has text in a language when its area file in that language
  exists and holds the code as a key with a string value.

```python
from flowchecks import error_codes

missing: list[str] = error_codes.check(translations_dir, codes)
```

- `translations_dir`: `str | os.PathLike[str]`, the `translations`
  folder itself; `codes`: `Iterable[str]`, the registered codes.
- It returns the codes of `codes` that have no text in `en` or no text
  in `vi`, each once, in any order; `[]` is a pass.
- The tests call only this function. How CI runs it (for example a
  command `python -m flowchecks.error_codes --translations <dir>` over
  `scan_modules().error_codes` and the platform's own codes, exiting
  non-zero and printing each missing code) is the code's choice and is
  not run by the tests.

| Case | Translations | Codes | Expected |
|------|--------------|-------|----------|
| F4-6 | `en/f4probe.json` has `f4probe.no_vietnamese_text` and `f4probe.has_both_texts`; `vi/f4probe.json` only the second; `en/f4nofile.json` has `f4nofile.no_vietnamese_file` and there is no `vi/f4nofile.json` | `scan_modules().error_codes`, holding all three from a temporary module's `register` | Holds `f4probe.no_vietnamese_text` and `f4nofile.no_vietnamese_file`, not `f4probe.has_both_texts` |
| F4-7 | `request.invalid` and `f4probe.has_both_texts` in `en` and `vi`; `en/f4probe.json` also holds an unregistered key | Those two codes | `[]` |

## F12: Contract publishing

`app/platform/contract.py` holds the stub helper, the generator of
`openapi.json`, and the example check (task F12, scope). The tests are
`tests/contract/platform/test_F12_contract.py` (level C) and
`tests/api/platform/test_F12_stub.py` (level A); their shared helpers are
in `tests/contract/platform/f12_support.py`.

### F12: Declaring a stub

A module's `api.py` declares a stub route with `stub()`:

```python
from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.platform.contract import stub

router = APIRouter()


class Place(BaseModel):
    model_config = ConfigDict(json_schema_extra={"examples": [{"name": "Da Lat"}]})

    name: str


@stub(router, "GET", "/<segment>", response_model=Place)
async def place() -> Place: ...
```

- `stub(router, method, path, **route_options)` returns a decorator that
  adds the decorated function to `router` as the route `method path`
  (`method` an upper-case HTTP method name). `route_options` are those of
  FastAPI's `router.add_api_route` (the tests pass `response_model`). The
  function's signature declares the request; its body is never what
  answers.
- Calling the route returns `501` with the error format of
  api-design.md, section 4.1 (`app/platform/errors.py`): a JSON object
  whose only key is `error`, whose keys are exactly `code`, `params`,
  `fields`, `requestId`, and whose `code` is `server.not_implemented`.
- In the contract, the operation object of the route
  (`paths["/v1/<segment>"]["get"]`) has `"x-stub": true`. An operation that
  is not a stub (for example `GET /v1/health`) has no `x-stub: true`.
- An example on a response schema is given as above, on the response
  model (`json_schema_extra={"examples": [...]}`, which pydantic writes as
  `examples` on the model's schema). A response model with no
  `json_schema_extra` has no example.

### F12: Generating `openapi.json`

```text
python -m app.platform.contract generate <output path>
```

- Run from the repository root with the project's Python. It writes the
  contract of the app (every module found by scanning `app/`, as
  `create_app()` builds it) to `<output path>` as JSON, and exits `0`.
  The written file has the key `openapi` and the path `/v1/health`.
- It works with `DATABASE_URL`, `REDIS_URL`, and `PORT` not set, and
  reaches neither PostgreSQL nor Redis (the CI job `contract` has no
  services). The tests set `RELEASE_VERSION` to a fixed value and no
  other app setting.
- The same app gives the same file: two runs in separate processes
  (with `PYTHONHASHSEED` `1` and `2`) write byte-identical files (F12-1).

### F12: The example check

```python
from app.platform.contract import check_examples

problems: list[str] = check_examples(contract)   # contract: dict, as read from openapi.json
```

```text
python -m app.platform.contract check <openapi.json path>
```

- `check_examples` takes the contract as a JSON object (the generated
  file read with `json.load`, unchanged) and returns one message per
  operation marked `x-stub: true` that has a response schema with no
  example; an empty list when there is none. It does not change its
  argument.
- A message names its operation as `<METHOD> <path>`: the upper-case
  method, one space, and the path as written in the contract, for
  example `GET /v1/places`. A message never names an operation whose
  response schemas all have an example.
- Operations that are not stubs are not checked (F12; F17 widens this,
  see "F17: Examples on every success response").
- `check` reads the file, prints the messages (stdout or stderr), and
  exits non-zero when there is one; it exits `0` when there is none.

| Case | Given | Expected |
|------|-------|----------|
| F12-1 | The app with no change, generated twice in separate processes | Byte-identical files; `openapi` and `/v1/health` in them |
| F12-2 (C) | A module folder whose `api.py` declares one stub with an example | The generated contract has `x-stub: true` on `GET /v1/<segment>`, and not on `GET /v1/health` |
| F12-2 (A) | The same module, the app built with `create_app()` (PostgreSQL and Redis on closed ports) | `GET /v1/<segment>` returns `501` with `{"error": {"code": "server.not_implemented", "params", "fields", "requestId"}}` |
| F12-3 | Control: one stub with an example. Then a second stub, `GET /v1/bare-<segment>`, whose response model has none | Control: `check_examples` gives `[]`, `check` exits `0`. Then: `check_examples` gives messages, one naming `GET /v1/bare-<segment>` and none naming `GET /v1/<segment>`; `check` exits non-zero and its output names the bare stub and not the other |

## F17: Examples on every success response

The tests are in `tests/contract/platform/test_F17_examples.py` (level
C). They use `generate`, `run_contract`, and `MODULE_PREFIX` of
`tests/contract/platform/f12_support.py`, and the `module_folder`
fixture, whose `api.py` declares plain FastAPI routes on a `router`
(no `stub()`, no access declaration: the contract is only generated).

- `check_examples(contract)` and `check` keep the F12 interface, and
  also check every operation that is not marked `x-stub: true`: a
  message names it as `<METHOD> <path>` when one of its `2xx` responses
  has content with no example, neither on the media type (`example` or
  `examples`) nor on its schema (after `$ref`).
- A `2xx` response with no `content` (such as `204`) needs no example.
- A `4xx` or `5xx` response of an operation that is not a stub is not
  checked.
- The app's own contract passes the check. `GET /v1/health` `200`
  `application/json` has an example, on the media type or on its
  schema, that is a JSON object with at least the keys `status` and
  `version`.

| Case | Given | Expected |
|------|-------|----------|
| F17-1 | A route `GET /v1/bare-<segment>`, not a stub, whose `200` response model has no example | `check_examples` gives a message naming it; `check` exits non-zero and its output names it |
| F17-2 | The app with no change | `check_examples` gives `[]`, `check` exits `0`; the health example has `status` and `version` |
| F17-3 | `DELETE /v1/gone-<segment>` answering `204` with no content, `GET /v1/good-<segment>` whose `200` model has an example, and the control `GET /v1/bare-<segment>` | The control is named; neither of the others is |
| F17-4 | `GET /v1/maybe-<segment>` with an example on `200` and a `404` response model with none, and the control `GET /v1/bare-<segment>` | The control is named; `maybe` is not |

## F5: Access

The tests are in `tests/api/platform/test_F5_access.py` (F5-1 to F5-10,
level A) and `tests/api/platform/test_F5_access_declaration.py` (the
declaration test itself, which CI runs over the real app). A temporary
module under `app/` (the `module_folder` fixture) serves the routes and
registers the session hook; the app is called in-process
(`client_for(build_app())`) with the settings of `set_settings()`, so no
route of the tests uses PostgreSQL or Redis, except F5-10, which uses the
real ones (as F1-1).

### F5: Declaring access

Every route declares its access with a FastAPI dependency from
`app.platform.access` (spec 2.2, row "Access declaration"; the names are
the tests' choice, as the spec names none):

```python
from typing import Annotated

from fastapi import APIRouter, Depends

from app.platform.access import Caller, public, signed_in

router = APIRouter()


@router.get("/open", dependencies=[Depends(public)])
async def open_route() -> dict[str, str]: ...


@router.get("/me")
async def me(caller: Annotated[Caller, Depends(signed_in)]) -> dict[str, str]:
    return {"userId": str(caller.user_id), "sessionId": str(caller.session_id)}
```

- `public`: a dependency that checks nothing (no token is read).
- `signed_in`: a dependency that runs the token check below and returns
  a `Caller`. `Depends(signed_in)` in `dependencies=[...]` declares the
  route too, for a handler that does not need the caller.
- `Caller`: `user_id: uuid.UUID` and `session_id: uuid.UUID`, the
  token's user ID and session ID (security.md, section 2: the access
  token holds only these two).
- A capability (security.md, section 3.2) is out of scope for F5; its
  form is left to EPIC-02.

### F5: The declaration test

```python
from app.platform.access import undeclared_routes

undeclared: list[str] = undeclared_routes(app)   # app = create_app()
```

- It returns one entry for each route of `app` that declares neither
  `public`, `signed_in`, nor a capability; each entry names the route by
  its full path (for example `GET /v1/<segment>/undeclared`). `[]` when
  every route declares its access. Whether FastAPI's own OpenAPI and docs
  pages are listed is the code's choice; they are not module routes, and
  the real app must give `[]`.
- `test_F5_every_route_declares_its_access` asserts
  `undeclared_routes(create_app()) == []` and puts the list in its
  failure message. It is the test that fails CI (spec 2.2).
- F5-1 runs that test in a new `python -m pytest <node id>` process from
  the repository root, with the test's environment: it exits `0` while
  the temporary module's routes all declare their access, and exits `1`
  once the module adds an undeclared route, with the route's path in its
  output and no declared route's path.

### F5: The device ID

`X-Device-Id: <uuid>` (api-design.md, section 2) is required on every
request except the health check `GET /v1/health`; missing, or not a
UUID (the tests send `not-a-uuid`), the answer is `400 request.invalid`
in the error format of F4. It is checked before the access declaration
(spec 2.2 runs the steps in order), so a `signed_in` route with a valid
token and no device ID is `400`, not `401`.

### F5: The token check

- The token comes as `Authorization: Bearer <access token>`
  (api-design.md, section 2).
- It is a JWT signed with EdDSA over Ed25519 (security.md, section 2),
  header `{"alg": "EdDSA", "typ": "JWT"}` with no `kid`, and these claims
  (the claim names are the tests' choice):

  | Claim | Value |
  |-------|-------|
  | `sub` | The user ID, a UUID as a string |
  | `sid` | The session ID, a UUID as a string |
  | `iat` | Issued at, seconds since the epoch |
  | `exp` | Expiry, seconds since the epoch; 15 minutes after `iat` unless a case says otherwise |

- The public keys the app trusts come from the setting
  `ACCESS_TOKEN_PUBLIC_KEYS`: one or more Ed25519 public keys, each a PEM
  block (`-----BEGIN PUBLIC KEY-----`, SubjectPublicKeyInfo), one after
  the other (security.md, section 2: a new public key is published next
  to the old one). The tests make a new key pair in each test and set
  only the public key, after `set_settings()`. The setting is optional
  for the app to start: the tests of other cards do not set it.
- A token is accepted only when its signature checks with one of the
  keys under `EdDSA` and the time is before `exp`, with no leeway
  (F5-8: one second past `exp` is expired).
- Then `identity` is asked whether the session is active, through the
  hook a module registers in its `register(registry)`
  (`app/platform/modules.py`):

  ```python
  # app/<module>/__init__.py
  async def session_is_active(session_id: uuid.UUID) -> bool: ...

  def register(registry):
      registry.set_session_hook(session_is_active)
  ```

  It is called with the token's `sid` as a `uuid.UUID` on every request
  to a non-`public` route whose token passed the checks above.

| Case | Given | Expected |
|------|-------|----------|
| F5-2 | `signed_in` route; valid token; hook says active | `200`; the handler's `Caller` holds the token's `sub` and `sid`; the hook was asked about `sid` |
| F5-3 | `public` route, no `Authorization` | `200` |
| F5-4 | `signed_in` route, no `Authorization` | `401 auth.not_signed_in` |
| F5-5 | `exp` 60 seconds ago | `401 auth.token_expired` |
| F5-6 | Signed with another Ed25519 key; or header `{"alg": "none"}` and an empty signature | `401 auth.not_signed_in`, both |
| F5-7 | Valid token; the hook returns `False` for its `sid` | `401 auth.token_expired` |
| F5-8 | `exp` at least one second ahead; then `exp` at least one second past | `200`, then `401 auth.token_expired` |
| F5-9 | `public` route, and `signed_in` route with a valid token: no `X-Device-Id`, or `not-a-uuid` | `400 request.invalid`, all four |
| F5-10 | `GET /v1/health`, no `X-Device-Id`, real PostgreSQL and Redis | `200`; on the same app, a `public` route with no `X-Device-Id` is `400 request.invalid` |

Each error body is exactly `{"error": {"code", "params", "fields",
"requestId"}}` (F4).

- F1-7's probe route (section "Modules found by scanning") declares no
  access; its requests carry a valid `X-Device-Id` from F5 on.

## F6: Rate limits

The tests are in `tests/api/platform/test_F6_rate_limits.py` (F6-1 to F6-5,
level A) and `tests/integration/platform/test_F6_rate_limits_unavailable.py`
(F6-6, level I), with their helpers in `tests/api/platform/f6_support.py`.
As in F5, a temporary module under `app/` (the `module_folder` fixture)
serves the routes and registers a session hook that says every session is
active; the app is called in process (`client_for(build_app())`). F6-1 to
F6-5 use the real Redis (`TEST_REDIS_URL`) and PostgreSQL on a closed port;
F6-6 uses the real PostgreSQL and Redis on a closed port.

### F6: The general ceiling

- The setting `RATE_LIMIT_PER_MINUTE` (spec 2.2, row "Rate limits"; 5.1):
  an integer, the requests a minute; `300` when it is not given. It is
  optional for the app to start. The tests set small values (2 to 4) to
  reach the ceiling; F6-3 leaves it out.
- `RATE_LIMIT_PER_MINUTE=0` turns the general ceiling off; a route's own
  limit still applies (spec 2.2; 5.1, version 2.8). Production never sets
  `0`. `test_F6_extra_ceiling_0_turns_the_general_ceiling_off_but_not_a_routes_own_limit`
  sends 301 requests (one over the default of 300) from one IP address,
  which all pass, and then reaches a route's own limit of 2, which answers
  `429 rate.limited`.
- The tests of earlier cards that call a route other than the health check
  set `RATE_LIMIT_PER_MINUTE="0"` with their other settings, so they pass
  with Redis on a closed port (or, on the real Redis, without sharing the
  counter of the in-process client's address `127.0.0.1`). Each such test
  is otherwise unchanged.
- It applies to every route except the health check, with no declaration
  in the route. Its window is 60 seconds.
- It is counted for the IP address and, on a route that is not `public`,
  also for the user (the token's `sub`); either count over the ceiling
  stops the request (spec 2.2; 5.1).
- The IP address is the last entry of `X-Forwarded-For` (entries split on
  `,`, spaces around them ignored), or the connection's address when the
  header is missing (spec 2.2; 5.1). Every test request carries its own
  made-up address from `10.0.0.0/8` as that last entry, so no two tests
  share a counter; F6-4 puts other addresses before it, which do not count.
- Rate limits come after the device ID and the token check (spec 2.2, order
  of the steps); every request of the tests carries a valid UUID
  `X-Device-Id` (F5).

### F6: A route's own limit

A route adds its own limit with a FastAPI dependency from
`app.platform.rate_limits` (spec 2.2: "A route can add its own limit with
its key"; the names are the tests' choice, as the spec names none):

```python
from fastapi import APIRouter, Depends, Request

from app.platform.access import public
from app.platform.rate_limits import rate_limit

router = APIRouter()


def by_probe_key(request: Request) -> str:
    return "<module name>:" + request.headers.get("X-Probe-Key", "none")


@router.get(
    "/limited",
    dependencies=[
        Depends(public),
        Depends(rate_limit(limit=2, window_seconds=10, key=by_probe_key)),
    ],
)
async def limited() -> dict[str, str]: ...
```

- `rate_limit(*, limit: int, window_seconds: int, key)` returns a
  dependency. `key` is a plain function that takes the `Request` and
  returns a string; requests with the same string share one counter of
  `limit` requests per `window_seconds`. The tests start each key with
  the module's unique name. Whether two declarations that return the same
  string share a counter is not tested.
- `rate_limit` is not an access declaration: the route still declares
  `public`, `signed_in`, or a capability (F5).
- The route's limit applies as well as the general ceiling (F6-5): either
  one over its limit stops the request.

### F6: Counting

- Counters live in Redis (`REDIS_URL`), one per key, in a fixed window that
  starts at the window's first request and lasts the window's length
  (spec 2.2; 5.1).
- Over a limit: `429 rate.limited` in the error format of F4 (api-design.md,
  section 4.2), with the header `Retry-After` set to the whole seconds left
  in the window, rounded up (spec 2.2; 5.1). The handler does not run.
  The tests accept any value between the seconds left rounded up as
  measured from the earliest and the latest moments the first request and
  the request over the limit could have been served. F6-2 puts each
  request over exactly one limit: the requests to the route with its own
  limit each come from a new IP address, so they stay under the ceiling.
- A request over more than one limit (the ceiling and a route's own limit)
  gets the longest of their waits in `Retry-After` (spec 2.2; 5.1, version
  2.8). `test_F6_extra_over_two_limits_gets_the_longest_wait` sends a third
  request from one IP address with one key, over a ceiling of 2 a minute and
  a route limit of 2 in 10 s, and accepts only the ceiling's seconds left.
- Whether a request that was stopped is counted is not tested.
- Redis does not answer (F6-6, a closed local port): every rate-limited
  route, so any route but the health check, returns `503
  server.unavailable` (spec 2.3; 5.1), and the handler does not run.

| Case | Given | Expected |
|------|-------|----------|
| F6-1 | Ceiling 3: three requests from one IP; route limit 2: two requests with one key | All `200`; every handler ran |
| F6-2 | Ceiling 2: a third request 1.3 s after the first; route limit 2 in 10 s: a third request 1.3 s after the first, each request from a new IP (under the ceiling) | `429 rate.limited`, `Retry-After` the seconds left of 60 s and of 10 s, rounded up; no handler ran for them |
| F6-3 | Route limit 2 in 2 s: a third request is `429`; then a request once 2 s have passed since the first | The last one `200` |
| F6-4 | Ceiling 2: IP A three times (other entries before it), then IP B twice; on the `signed_in` route, user 1 from three new IPs, then user 2 from the third IP and from a new one | A: `200`, `200`, `429`; B: `200`, `200`; user 1: `200`, `200`, `429`; user 2: `200`, `200` |
| F6-5 | Ceiling 4, route limit 2: key `a` three times and key `b` once from one IP; five new keys from another IP | `200`, `200`, `429`, `200`; then four `200` and a `429` |
| F6-6 | Redis on a closed port: a `public` route, a `signed_in` route with a valid token, the route with its own limit | `503 server.unavailable`, all three; no handler ran |
| Extra | Ceiling 2 and route limit 2 in 10 s: a third request from one IP with one key, 1.3 s after the first, over both | `429 rate.limited`, `Retry-After` the seconds left of 60 s (the longest wait), rounded up; no handler ran for it |
| Extra | Ceiling `0`: 301 requests from one IP; route limit 2: three requests with one key | All 301 `200`; then `200`, `200`, `429 rate.limited` |

## F10: Observability

The tests are in `tests/unit/platform/test_F10_log_lines.py` (F10-1 and
F10-2, level U: PostgreSQL and Redis on closed local ports, the general
ceiling off) and `tests/integration/platform/test_F10_observability.py`
(F10-3 to F10-6, and the F10-2 checks of spans, outgoing calls, and
the log line of a database error, which need the span exporter or the
database; level I: the real PostgreSQL and Redis of
`TEST_DATABASE_URL` and `TEST_REDIS_URL`, the database at the head
migration). Nothing in them reaches a real network: Sentry, the
collector, and the exporters are in-memory objects the tests pass in, or
local sockets the tests open themselves. The names below are the tests'
choice where the spec names none.

### F10: Settings

Spec 2.6 (row "Settings") and 5.1, version 3.3. All three are optional at
start-up; the settings of the table in "Settings" above keep their rules.

| Name | Required | Meaning |
|------|----------|---------|
| `SENTRY_DSN` | No | The Sentry DSN (a secret). Without it nothing goes to Sentry. |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | No | The OTLP/HTTP base address of the collector (Grafana Cloud), `http://` or `https://`. Without it no trace or metric leaves the process. |
| `OTEL_EXPORTER_OTLP_HEADERS` | No | The headers sent with each export, as OpenTelemetry writes them: `key=value` pairs separated by `,`, values URL-encoded (a secret: Grafana Cloud's `Authorization`) |

- A value that is given is checked; a malformed one stops start-up and
  the error names the setting and never its value (the rules of
  "Settings" above). No F10 case tests this.
- `.env.example` lists the three with no value.
- The release tag sent to Sentry is `RELEASE_VERSION`.

### F10: Passing in-memory transports

```python
from app.main import create_app

app = create_app(
    sentry_transport=transport,   # a sentry_sdk.transport.Transport
    span_exporter=spans,          # an opentelemetry SpanExporter, e.g. InMemorySpanExporter()
    metric_reader=reader,         # an opentelemetry MetricReader, e.g. InMemoryMetricReader()
)
```

- `create_app(settings=None, *, sentry_transport=None, span_exporter=None,
  metric_reader=None)`: the three keywords are optional and default to
  the real transports. The settings are still read from the environment
  when `settings` is not given. (The code may also offer them through
  `app.platform.logging.install_observability`; the tests call only
  `create_app`.)
- `sentry_transport`: with `SENTRY_DSN` set, every Sentry envelope goes
  to `transport.capture_envelope(envelope)` in place of the network. The
  tests set `SENTRY_DSN` to `http://<random key>@127.0.0.1:<closed port>/1`.
- `span_exporter`: every finished span of the app goes to it in place of
  the OTLP exporter, whether `OTEL_EXPORTER_OTLP_ENDPOINT` is set or not.
- `metric_reader`: the app's metrics are read through it in place of the
  OTLP exporter, whether `OTEL_EXPORTER_OTLP_ENDPOINT` is set or not.
- Each call of `create_app` routes its app's signals to the objects
  passed to that call, even when one test process builds several apps
  (OpenTelemetry's global providers can be set only once, so the code
  must not depend on them for this).
- Spans and Sentry envelopes are handed over at the latest when the app's
  lifespan has ended (`client_for` has exited): shut-down flushes them.
  The tests read them after that.
- Metrics are read while the app runs (inside `client_for`), with
  `reader.get_metrics_data()` called in a worker thread
  (`asyncio.to_thread`), so the app's event loop keeps running while the
  values are collected. The tests read again, up to 10 s, until the value
  they wait for is there.

### F10: Log lines

- Every log line of the API process is one JSON object on one line on
  standard output (`structlog`). The tests capture it with pytest's
  `capfd`, so it does not matter whether the logger writes to
  `sys.stdout` or to file descriptor 1.
- Each HTTP request writes exactly one line whose `"event"` is
  `"request"`, once its response has been sent, with these keys:

  | Key | Value |
  |-----|-------|
  | `event` | `"request"` |
  | `request_id` | The request ID, equal to the response's `X-Request-Id` |
  | `user_id` | The signed-in caller's user ID (the token's `sub`, as text), on a request whose route checked the token and passed; absent or `null` otherwise |
  | `trip_id` | The trip ID when the route has a path parameter `trip_id` (its value as sent in the path); absent or `null` otherwise |
  | `method` | The HTTP method |
  | `path` | The URL path, with no query string |
  | `status` | The response status, a number |
  | `duration_ms` | The time to answer, a number |

- No log line holds an email address, a name, a password, a token, a
  header value of `Authorization`, or any part of the request body (spec
  2.6). F10-2 looks for the values in all captured standard output and
  standard error.
- Other lines (start-up, an unexpected error) are JSON too and carry
  `request_id` when they are written during a request.
- A database error raised during a request (an SQLAlchemy exception that
  wraps an asyncpg one) is answered `500 server.error` and logged without
  any value from the database call: no bound parameter value (SQLAlchemy's
  `[parameters: ...]`, the statement's values) and no value the database
  quotes in its message or its `DETAIL` (for example
  `invalid input syntax for type integer: "<value>"` or
  `Key (email)=(<value>) already exists.`). Its error line, a JSON line
  whose `event` is not `"request"`, keeps the request ID (`request_id`
  equal to the response's `X-Request-Id`) and the error type: the name of
  the exception's class (`type(error).__name__`, for example `DataError`
  or `IntegrityError`) appears in the line. F10-2 looks for the values in
  all captured standard output and standard error (`capfd`) and in every
  record that reaches the root logger (`caplog`: its text, each record's
  message, arguments, and fields, a formatted traceback included), and in every
  span of the request (an in-memory span exporter; section "F10:
  Traces"), which holds a `SERVER` span and a `postgresql` query span. Sentry
  still receives the full error (F10-3); F10-2 asserts nothing about
  Sentry here and sets no `SENTRY_DSN`.
- The tests' route for this uses `get_session` (section "F3: Database
  session") in a temporary module's `api.py`, catches the exception only
  to keep `type(error).__name__` in a module list, and raises it
  unchanged.

### F10: An error after the response has started

- An exception raised after the response has started (a
  `StreamingResponse` whose generator raises after its first chunk, or a
  `BackgroundTasks` task that raises once the response is sent) never
  leaves the ASGI app: the app handles it itself. Under uvicorn (as
  `python -m app.main` runs it) an exception that escapes the app is
  logged by uvicorn as "Exception in ASGI application", with the full
  traceback and the exception's message and with no `request_id`; the app
  must not let that happen.
- The error is logged as a JSON line whose `event` is not `"request"`,
  with `request_id` equal to the response's `X-Request-Id` (sent with the
  response's start) and the exception's class name (`ValueError`) in the
  line. No captured output (`capfd`) and no record that reaches the root
  logger (`caplog`: its text, each record's message, arguments, fields,
  `exc_info` formatted as a traceback, and `exc_text`) holds a value from
  the exception's message.
- The error is recorded on a span of the request as an event named
  `exception`, and no span (attributes, status description, events, their
  `exception.message` and `exception.stacktrace` included) holds a value
  from the exception's message.
- How the tests observe it (their choice): they call the ASGI app itself,
  `await app(scope, receive, send)`, inside `app.router.lifespan_context(app)`,
  with an HTTP `scope` of ASGI spec version `2.3` and a `receive` that
  gives an empty body and then waits for the disconnect; they assert that
  no exception escapes that call, within 5 s. They do not start uvicorn:
  an exception that escapes the call is exactly what uvicorn would log.
  `httpx.ASGITransport` is not used here, because it runs no server logger.
- The tests' module raises `ValueError(f"bad user {EMAIL} {TOKEN}")`, with a
  random email and token written into its `api.py`; the request carries
  neither value. No `SENTRY_DSN` is set.

### F10: Error reporting

- An exception a handler does not handle (answered `500 server.error`
  by the request ID middleware of F4) is sent to Sentry as an event
  whose `exception` holds the exception's message, whose `release` is
  `RELEASE_VERSION`, and whose `tags` hold `request_id` equal to the
  response's `X-Request-Id`.
- The tests read the events with `envelope.get_event()` on each envelope
  the transport received, and look for the one whose exception value
  holds the random message the handler raised.

### F10: Traces

- The API's requests, its SQLAlchemy queries, its Redis commands, and
  its outgoing HTTP calls are traced with OpenTelemetry (spec 2.6; 4.1).
- One request is one trace: one span of kind `SERVER` for the request,
  and its queries as spans of the same trace. A query span carries the
  attribute `db.system` or `db.system.name` with the value
  `postgresql`.
- The `SERVER` span of a request carries `http.route`, the route's
  template (for example `/v1/<segment>/items/{item_id}`), never the path
  with its values: no span's `http.route` holds a path parameter's value.
- F10-4 uses `GET /v1/health`, which reads PostgreSQL and Redis. The app
  starts and stops without a span of its own: every span the exporter
  holds after the one request has that request's trace ID.
- No span holds a query string, a token or an email — not in its
  attributes, its status description, or its events (spec 2.6, decision
  of 3.3: no email, token, or request body). A URL in an attribute
  (`http.url`, `url.full`, `http.target`, `url.query`, ...) is kept
  without its query, or the query is left out. A failing query's span
  may record the error (status `ERROR`, an `exception` event), but its
  status description and the event's attributes (`exception.message`,
  `exception.stacktrace`, ...) hold no bound value and no value the
  database quotes. The tests send random values and look for them in the
  text of every span: its name, every attribute, `status.description`,
  each event's name and attributes, and each link's attributes.
- OpenTelemetry's header capture setting is not followed for
  `Authorization`: with `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST=Authorization`
  in the environment when `create_app` is called (and
  `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SANITIZE_FIELDS` not set), a
  request with `Authorization: Bearer <token>` leaves the token in no span.
  Whether the app ignores the setting or masks the header's value is left
  open.

### F10: Outgoing HTTP calls

```python
import httpx

async with client_for(app):                       # the app's lifespan runs
    async with httpx.AsyncClient() as raw:
        client = app.state.observability.instrument_http_client(raw)
        await client.get("http://127.0.0.1:<port>/f10-ping?key=<random>")
```

- `app.state.observability` is set by `create_app` (it may exist before
  the lifespan starts; the tests read it while the lifespan runs). Its
  `instrument_http_client(client: httpx.AsyncClient) -> httpx.AsyncClient`
  returns the client to use (it may be the same object, instrumented in
  place). Every module that calls out over HTTP uses a client made so.
- Each call through it makes one span of kind `CLIENT`, sent to the
  app's span exporter (the one passed to `create_app` in the tests).
- An outgoing URL is never logged with its query, and never put in a
  span attribute with it: not in the app's own lines, and not in
  `httpx`'s own `INFO` line (`HTTP Request: GET <url> ...`), which the app
  either leaves out or writes without the query. The tests look in the
  captured standard output and standard error (`capfd`) and in the
  records of loggers named `httpx...` that reach the root logger
  (`caplog`, with the levels the app set; the test does not change them).
- The tests' local server answers `200` and writes no log line of its
  own.

### F10: Metrics

Pushed with the OpenTelemetry metrics exporter to the collector of
`OTEL_EXPORTER_OTLP_ENDPOINT`; there is no metrics route (spec 2.6; 5.1).

| Metric | Kind | Unit | Attributes | Value |
|--------|------|------|------------|-------|
| `http.server.request.duration` | Histogram | `s` | `http.route` (the route's template, e.g. `/v1/health`), `http.request.method`, `http.response.status_code` | One data point per route, method, and status; its `count` is the number of requests (the rate) and its `sum` their time (the latency); a status of `500` is an error |
| `tripmate.queue.depth` | Gauge (or UpDownCounter) | `{job}` | none needed | Jobs waiting in the queue: `0` until F9 adds the queue and feeds it |
| `tripmate.job.age` | Gauge | `s` | none needed | Age of the oldest waiting job: `0` until F9 feeds it |
| `tripmate.outbox.waiting` | Gauge (or UpDownCounter) | `{event}` | none needed | Rows of `outbox_events` whose `published_at` is null |

- The tests read a metric by its name from every scope of
  `get_metrics_data()`. For the three gauges they take the value of the
  last data point (with no attributes, there is one).
- F10-6 inserts two unpublished rows and one published row in
  `outbox_events` through a connection of its own, before the app starts,
  and waits (up to 10 s) for `tripmate.outbox.waiting` to read `2`.

### F10: A collector or Sentry that does not answer

- F10-5 opens two local TCP sockets that accept connections and never
  read or answer, and sets `SENTRY_DSN` to
  `http://<random key>@127.0.0.1:<port 1>/1` and
  `OTEL_EXPORTER_OTLP_ENDPOINT` to `http://127.0.0.1:<port 2>`, with
  `OTEL_EXPORTER_OTLP_HEADERS` set to a made-up `Authorization`. No
  in-memory object is passed: the real transports are used.
- A request to the error route answers `500 server.error`, and then
  `GET /v1/health` answers `200`, each within 5 s.
- To show that both were asked, each socket receives at least one
  connection within 15 s after the app's lifespan has ended.
- Shut-down is bounded: with both sockets silent, after those two
  requests, the app's lifespan ends (`client_for` exits) within 15 s.
- Shut-down does not block the event loop: while the lifespan ends, a
  task of the test that wakes every 50 ms never waits 1 s or more between
  two wake-ups (flushing Sentry and the exporters runs off the event loop
  or with a bounded wait that yields).

| Case | Given | Expected |
|------|-------|----------|
| F10-1 | `GET /v1/platform/whoami` with a valid token (a session hook that says active), and `GET` of a `public` module route without a token | One `request` line each, `request_id` equal to `X-Request-Id`; the first with `user_id` equal to the token's `sub`, the second without a `user_id` |
| F10-1 | `GET` of a `public` module route `/v1/<segment>/trips/{trip_id}` with a random UUID, and `GET` of a `public` module route without `trip_id` | One `request` line each; the first with `trip_id` equal to the UUID, the second without a `trip_id` |
| F10-2 | `POST` of a `public` module route `.../sign-in` with a JSON body holding an email and a password, and `Authorization: Bearer <random token>` | One `request` line; no captured output holds the email, the password, or the token |
| F10-2 | `GET /v1/health?token=QSECRET<random>&email=<random>@example.com`, an in-memory span exporter | `200`; a `SERVER` span; no span (attributes, status description, events) and no captured output holds the token or the email |
| F10-2 | An outgoing `GET` through `instrument_http_client` to a local server, with `?key=SECRETKEY<random>` | `200`; a `CLIENT` span; no span (attributes, status description, events), no captured output, and no `httpx` log record holds the key |
| F10-2 | A module route (`get_session`) whose statement `SELECT CAST(CAST(:email AS text) AS int), CAST(:token AS text)` fails with a random email and token as bound values, an in-memory span exporter | `500 server.error`; no captured output, no log record, and no span (attributes, status description, events) holds the email or the token; a `SERVER` span and a `postgresql` query span; a JSON error line carries `request_id` = `X-Request-Id` and the exception's class name |
| F10-2 | A module route (`get_session`) that stores a random email and token in a temporary table, then fails with a unique violation on the email (a statement with no bound value; `DETAIL` quotes the email), an in-memory span exporter | The same |
| F10-2 | A module route whose `StreamingResponse` raises `ValueError` with a random email and token after its first chunk; and one whose background task raises the same; the ASGI app called directly | The response started with `200` (and the first chunk); no exception escapes the app; no captured output and no log record (traceback included) holds the email or the token; a JSON error line carries `request_id` = `X-Request-Id` and `ValueError` |
| F10-2 | The same two routes, an in-memory span exporter | A `SERVER` span; some span has an `exception` event; no span (attributes, status description, events) holds the email or the token |
| F10-2 | `GET /v1/health` with `Authorization: Bearer <random token>`, `OTEL_INSTRUMENTATION_HTTP_CAPTURE_HEADERS_SERVER_REQUEST=Authorization`, an in-memory span exporter | `200`; a `SERVER` span; no span and no captured output holds the token |
| F10-2, F10-6 | `GET` of a `public` module route `/v1/<segment>/items/{item_id}` with a random `item_id`, an in-memory span exporter and metric reader | `200`; the one `SERVER` span has `http.route` = the template; `http.server.request.duration` has a point for the template with status `200`; no `http.route` of a span or a point holds the random ID |
| F10-3 | A module route that raises `RuntimeError(<random message>)`, `SENTRY_DSN` set, an in-memory transport | `500`; an event with the message, `release` = `RELEASE_VERSION`, tag `request_id` = `X-Request-Id` |
| F10-4 | `GET /v1/health` with the real services, an in-memory span exporter | `200`; one `SERVER` span, at least one `postgresql` query span, all spans in one trace |
| F10-5 | Sentry and the collector on local sockets that never answer | The error route `500` and the health check `200`, each within 5 s; both sockets were connected to |
| F10-5 | The same, then the app's lifespan ends | It ends within 15 s; a 50 ms ticker never waits 1 s or more meanwhile; both sockets were connected to |
| F10-6 | Two unpublished and one published outbox rows, `GET /v1/health` and the error route once each, an in-memory metric reader | `http.server.request.duration` has a point for `/v1/health` with status `200` and one for the error route with status `500`, each `count` ≥ 1 and `sum` > 0; `tripmate.queue.depth` and `tripmate.job.age` read `0`; `tripmate.outbox.waiting` reads `2` |
