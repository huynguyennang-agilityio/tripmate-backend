# tripmate-backend

The TripMate backend: the FastAPI app, the worker, the scheduler, and
migrations. This file holds only what is about this repository's code:
setup, commands, and coding conventions.

## Read first

The product documents live in `../tripmate-docs/` (cloned side by side)
and are never copied here:

- `../tripmate-docs/GLOSSARY.md`: the terms; use them in code and names.
- `../tripmate-docs/prd.md`: requirements and their IDs.
- `../tripmate-docs/docs/architecture.md`: modules and their rules,
  background work, hosting.
- `../tripmate-docs/docs/api-design.md`, `data-model.md`,
  `security.md`, `test-strategy.md`, `development-flow.md`.
- `../tripmate-docs/docs/specs/<epic>/spec.md`: the spec of the epic
  being built, with each card's test cases.

The shared rules every session follows (language, decisions, status,
secrets) are in `../tripmate-docs/CLAUDE.md`, section "Working on
several machines".

## Setup

- Python 3.13 and [uv](https://docs.astral.sh/uv/).
- `uv sync` installs every dependency from `uv.lock`.
- The tests need a real PostgreSQL and Redis: `docker compose up -d`
  (`compose.yaml` runs only these two), or your own.
- When a PostgreSQL already runs on port 5432 (as on Mac 1), start it
  with another host port, for example
  `POSTGRES_HOST_PORT=5433 docker compose up -d`, and use that port in
  `TEST_DATABASE_URL`.
- The tests empty every table of the database `TEST_DATABASE_URL` names, so
  its name must end in `_test` or they fail at once. Compose creates
  `tripmate_test` when its volume is first made; on an older volume or your
  own PostgreSQL, run `createdb -h 127.0.0.1 -U tripmate tripmate_test`.

## Commands

| What | Command |
|------|---------|
| Tests | `TEST_DATABASE_URL=postgresql+asyncpg://tripmate@127.0.0.1:5432/tripmate_test TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run pytest tests -q` |
| Lint and format | `uv run ruff check .` and `uv run ruff format --check .` |
| Types | `uv run mypy app flowchecks` |
| Module boundaries | `uv run lint-imports` |
| Local PostgreSQL and Redis | `docker compose up -d` |
| One process, from the repository root | `python -m app.main`, `python -m app.worker`, `python -m app.scheduler` (the image's default command is the API) |

The tests are not safe to run in parallel. A test that needs PostgreSQL
or Redis fails, never skips, when `TEST_DATABASE_URL` or
`TEST_REDIS_URL` is missing.

## CI

`.github/workflows/ci.yml` runs on every pull request to `main` and on
every push to `main`. Each job is a check that is required on `main`
(EPIC-11 spec, section 2.9); the check's name is the job's name:

| Job | Runs |
|-----|------|
| `lint` | `ruff check .` and `ruff format --check .` |
| `types` | `mypy app flowchecks` |
| `unit` | `pytest tests/unit` (unit and property tests) |
| `integration` | `pytest tests --ignore=tests/unit` (everything but the unit tests) against PostgreSQL 16 and Redis 7 service containers |
| `imports` | `lint-imports` |
| `migrations` | exactly one Alembic head, `alembic upgrade head`, `alembic check` |
| `audit` | `pip-audit` on the locked dependencies |
| `secrets` | gitleaks over the commits of the change and the checked-out files |
| `image` | `docker build` of the `Dockerfile`, no push |

The four flow checks are in `.github/workflows/flow.yml` (a call of the
reusable `flow-checks.yml`) and `risk-gate.yml` (section "Flow checks"). Checks of later cards
join these files in their own cards. Third-party
actions are pinned to a full commit SHA with the version in a comment.

## Flow checks

`.github/workflows/flow.yml` runs the four checks of development-flow.md,
section 2.3, on every pull request to `main` (EPIC-11 spec, section 2.12).
The code is the `flowchecks/` package (`failing_first`, `test_lock`,
`mutation`, `risk_gate`), offered to the other code repositories as the
reusable workflow `.github/workflows/flow-checks.yml`.

The checks that judge a pull request are never the pull request's own:
`flow.yml` (on `pull_request`, so it can run the head's tests) runs the
`flowchecks` code of the base commit, and the risk gate is its own workflow,
`.github/workflows/risk-gate.yml` on `pull_request_target`, a file the pull
request cannot edit. It checks out only the base, fetches the head's objects,
and diffs them; it never runs anything from the head (the header of
`risk-gate-check.yml` explains why that is safe). The four required checks on
`main` are exactly `flow / failing-first`, `flow / test-lock`,
`flow / mutation`, and `gate / risk-gate`. `flow / risk-gate` exists but is
always skipped here; never require it.

| Check | Fails when |
|-------|------------|
| `failing-first` | A test that a test commit (trailer `Agent: test`) adds passes at that commit. Each test commit is checked out and run in turn; a commit that only changes tests is not asked to fail. Only pytest exit 1 or 2 counts as failing first; any other exit is an error of the run and fails the check. Runs against PostgreSQL 16 and Redis 7. |
| `test-lock` | A commit without the trailer changes, skips, or deletes a locked test (its function or method, with the module and class context it uses: imports, constants, helpers, `pytestmark`) or a locked support file; the head is compared with the last locked state too, so a merge cannot hide a change. A test commit that touches a path outside `tests/` fails too. A new test a code commit adds is not locked. |
| `mutation` | A changed rule file has a surviving, untested, or unchecked mutant (mutmut), or gains a `# pragma: no mutate` or `no cover`, that the pull request description does not list |
| `risk-gate` | A High path changed and the pull request has no `human-approved` label |

- The path list is `.github/risk-paths.toml`: `[[rule]]` tables with a
  `glob`, a `level` (High, Medium, or Low), and a `reason`. The highest level
  of the matching rules applies; a path no rule names is High. The path list,
  `.github/workflows/**`, every `CLAUDE.md`, and `.claude/agents/**` are High
  whatever the file says. Its Medium rules name the rule files of
  architecture.md, section 4, the files mutation testing runs on.
- `human-approved`: only the product owner adds this label to a pull
  request, to approve a High change. Claude Code never adds it. A push to the
  pull request removes it (the gate's job `revoke-approval`, on
  `synchronize` only; `labeled`, `unlabeled`, and `edited` never do), and the
  gate then judges that push as unapproved; the product owner adds the label
  again after reading the new commits.
- Which paths are High: the list of development-flow.md, section 2.1, as
  `.github/risk-paths.toml` maps it, including each module's `schemas.py`,
  `api.py`, and `events.py`, all of `app/platform/`, `app/main.py`,
  `app/worker.py`, `app/scheduler.py`, and `tests/requirement-ids.txt`.
- A surviving mutant is listed in the pull request description under
  `## Surviving mutants`, one line each: `- <mutant name>: <reason>` (the
  name as mutmut prints it, for example `app.money.splits.x_allocate__mutmut_3`).
  A pragma added to a rule file is listed as `- <path>#pragma: <reason>`.
  Editing the description runs the check again.
- The lock covers test functions and the `test*` methods of `Test*` classes
  (`path::TestX::test_y`), and every other file under `tests/` that a test
  commit added or changed (`conftest.py`, helpers, data). Known limits: helper
  methods inside a class, and fixtures or helpers outside `tests/`, are not
  locked; a file only a code commit added under `tests/` is not locked.
- A fix to `flowchecks/` (or to the workflows) is judged by the code of the
  base commit, so a change that repairs a broken check is judged by the broken
  check. When that check blocks the fix, the product owner merges with an admin
  merge after reading the diff.
- Run one check by hand from the repository root, for example
  `uv run python -m flowchecks.risk_gate --rules .github/risk-paths.toml --repo . --base origin/main --head HEAD`.
- Other repositories call `flow-checks.yml` with their own path list and test
  command, and pin `flowchecks-ref` to a tag or commit of this repository
  (the header of the workflow explains why). They run the risk gate as the
  backend does: `run-risk-gate` is false by default, and a
  `pull_request_target` workflow calls `risk-gate-check.yml` (its caller job
  needs `pull-requests: write` for the label removal).

## Settings

Environment variables only, read once at start-up and checked; the app
never reads a `.env` file. `.env.example` lists every setting with no
value. An error names the setting and never prints its value. Add a new
setting to `app/platform/config.py` and `.env.example` together.

## Layout and conventions

- `app/platform/` is shared by every module, one file per part
  (access, errors, rate limits, idempotency, outbox, jobs, config, db,
  logging, health, module scanning). It imports no module.
- `app/<module>/` follows the shape of EPIC-11 spec, section 2.1:
  `api.py` (with a `router`), `schemas.py`, `service.py`, rule files,
  `repository.py`, `models.py`, `jobs.py`, `events.py`.
- Modules are found by scanning `app/`: a new module changes no shared
  file. Its `router` is served under `/v1`. Its jobs, event handlers,
  due-work queries, and error codes go through a `register(registry)`
  function in its `__init__.py` (`app/platform/modules.py`); the API,
  the worker, and the scheduler run the same scan.
- A module imports another module's `service.py` only; `lint-imports`
  enforces this and forbids cycles between modules.
- Errors are raised as `AppError` (`app/platform/errors.py`) and
  returned in the format of api-design.md, section 4.1.
- mypy runs in strict mode; Ruff formats and lints (line length 100).
- Tests are named with their requirement or case ID, for example
  `test_F1_4_...`, and live under `tests/<level>/<module>/`.

## How a card is built

Each card runs with four agents in `.claude/agents/`: `test-agent`,
`code-agent`, `test-check-agent`, and `diff-review-agent`
(development-flow.md, section 2.3). The tasks and their test cases are
in `tasks/<epic>.md`; the notes of review round 2 go in
`tasks/<epic>-notes/<card>.md`.

Each agent's model is set in its role file: `code-agent` runs on Sonnet;
`test-agent`, `test-check-agent`, and `diff-review-agent` run on Opus
(development-flow.md, section 2.3). Do not override it when starting an
agent.

- Branch `<type>/<ID>-<short-name>`; every commit message and pull
  request title starts with the ID (development-flow.md, section 2.5).
- The test commit carries the trailer `Agent: test`. No other commit
  changes, skips, or deletes its tests.
- Commit no secret and no `.env`: the repository is public.
