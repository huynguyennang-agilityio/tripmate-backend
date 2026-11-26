# Test interface

What the tests need from the code, and nothing more, card by card:

- F1 Backend skeleton and health check: from the EPIC-11 spec, sections
  2.1, 2.7, and 7.1 (cases F1-1 to F1-8). Every section below up to
  "F3" is F1's.
- F3 Database session and migrations: from the EPIC-11 spec, sections
  2.2 (Transaction) and 4.2 (cases F3-1 to F3-5), in the sections whose
  names start with "F3".
- F14 Flow checks and agent roles: from the EPIC-11 spec, section 2.12,
  development-flow.md, sections 2.1 and 2.3, and the task's scope
  (cases F14-1 to F14-11; F14-12 is a manual check), in the sections
  whose names start with "F14".
- F13 Requirement-to-test report: from the EPIC-11 spec, section 2.12
  (row "Requirement-to-test report"), test-strategy.md, sections 1 and
  5, and the task's scope, version 1.5 (cases F13-1 to F13-5), in the
  sections whose names start with "F13".

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
