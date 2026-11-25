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

## Commands

| What | Command |
|------|---------|
| Tests | `TEST_DATABASE_URL=postgresql+asyncpg://tripmate@127.0.0.1:5432/tripmate TEST_REDIS_URL=redis://127.0.0.1:6379/0 uv run pytest tests -q` |
| Lint and format | `uv run ruff check .` and `uv run ruff format --check .` |
| Types | `uv run mypy app` |
| Module boundaries | `uv run lint-imports` |
| Local PostgreSQL and Redis | `docker compose up -d` |
| One process, from the repository root | `python -m app.main`, `python -m app.worker`, `python -m app.scheduler` (the image's default command is the API) |

The tests are not safe to run in parallel. A test that needs PostgreSQL
or Redis fails, never skips, when `TEST_DATABASE_URL` or
`TEST_REDIS_URL` is missing.

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
