# tripmate-backend -- EPIC-11 Tasks: Platform Foundation

**Document type:** Task file\
**Epic:** EPIC-11, first part, Phase 1\
**Repository:** `tripmate-backend`\
**Machine:** Mac 1\
**Spec:** [EPIC-11 spec](https://github.com/huynguyennang-agilityio/tripmate-docs/blob/main/docs/specs/epic-11/spec.md)\
**Status:** Proposed\
**Version:** 1.4

The tasks of EPIC-11 in this repository, in the order they start. Each task holds everything its Trello card will say ([development-flow.md](https://github.com/huynguyennang-agilityio/tripmate-docs/blob/main/docs/development-flow.md), section 1). The behaviour, the design, the order of the cards across repositories, and the checkpoints are in the spec, section 6.

Test case levels are the letters of [test-strategy.md](https://github.com/huynguyennang-agilityio/tripmate-docs/blob/main/docs/test-strategy.md), section 2. A case of level M has no automated test; it is shown once by hand and listed under Manual checks.

| Task | Title | Size | Risk | Depends on | Wave |
|------|-------|------|------|------------|------|
| [F1](#f1) | Backend skeleton and health check | M | High | -- | 1 |
| [F2](#f2) | Backend CI | S | High | F1 | 1 |
| [F14](#f14) | Flow checks and agent roles | M | High | F2 | 1 |
| [F3](#f3) | Database session and migrations | S | High | F1 | 2 |
| [F13](#f13) | Requirement-to-test report | S | High | F2 | 2 |
| [F4](#f4) | Error format and error codes | S | High | F1, CT1 | 3 |
| [F8](#f8) | Outbox | S | High | F3 | 3 |
| [F12](#f12) | Contract publishing | S | High | F2, CT1 | 3 |
| [F5](#f5) | Access declaration and token check | M | High | F4 | 4 |
| [F6](#f6) | Rate limits | S | Low | F5 | 4 |
| [F7](#f7) | Idempotency keys | M | High | F3, F5 | 4 |
| [F10](#f10) | Logs, traces, metrics, and error reporting | M | High | F4 | 4 |
| [F9](#f9) | Worker, scheduler, and cleanup | M | High | F7, F8 | 5 |
| [F11](#f11) | Local stack, image, and production on Render | M | High | F9, F10 | 5 |

## F1

**F1 Backend skeleton and health check**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 1 |
| Size | M |
| Risk | High: it adds every dependency of 4.1, the image, and `CLAUDE.md` and the agent roles |
| Depends on | -- |
| Requirements | Business rules 3 and 4, access (health check is public) |
| Spec sections | 2.1, 2.7, 4.1, 4.3 |
| Status | Review: [tripmate-backend pull request 1](https://github.com/huynguyennang-agilityio/tripmate-backend/pull/1) |

**Scope**

- The layout of 2.1, with one file for each part of `platform` (access, errors, rate limits, idempotency, outbox, jobs, logging) holding only its interface, so each later card fills its own file.
- Modules found by scanning `app/`; a module registers its routes, jobs, event handlers, due-work queries, and error codes at start-up.
- The API, the worker, and the scheduler start from one image with their own commands.
- Settings read once from environment variables at start-up and checked; `.env.example` lists every setting with no value.
- `GET /v1/health`, `public`: `200` with the release version, or `503 server.unavailable`.
- Every library of 4.1 in the lock file.
- `compose.yaml` with PostgreSQL and Redis; `CLAUDE.md`, `.gitattributes`, and the four agent roles in `.claude/agents/` (4.3).

**Leaves out**

- CI (F2), the database session and migrations (F3), and every part of `platform` beyond its interface: F4 to F10.
- The whole stack in Docker with seed data, and production: F11.

**Done when**

The layout of 2.1, with one file per part of `platform`, modules found by scanning, and every dependency of 4.1 in the lock file; the three processes start from one image; settings checked at start-up; `compose.yaml`; `GET /v1/health`; `CLAUDE.md`, `.gitattributes`, and the four agent roles (4.3)

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F1-1 | Main | PostgreSQL and Redis answer | `GET /v1/health` returns `200` with the release version | A |
| F1-2 | Error | PostgreSQL does not answer | `503 server.unavailable` | I |
| F1-3 | Error | Redis does not answer | `503 server.unavailable` | I |
| F1-4 | Error | A required setting is missing at start-up | The process stops and names the setting, never a value | U |
| F1-5 | Edge | A setting has a value of the wrong type | The process stops and names the setting | U |
| F1-6 | Main | The one image is started as API, as worker, and as scheduler | Each process starts and stays up | I |
| F1-7 | Main | A new module folder with an `api.py` is added | Its route is served, and no file outside the folder changed | I |
| F1-8 | Edge | A module folder with no `api.py` | The app starts; the folder is skipped | U |

**Manual checks**

None.

## F2

**F2 Backend CI**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 1 |
| Size | S |
| Risk | High: it adds the CI workflow |
| Depends on | F1 |
| Requirements | -- |
| Spec sections | 2.9 |

**Scope**

- A workflow that runs on every pull request every backend check of 2.9 that has code to run: Ruff, mypy, unit and property tests, integration and API tests against PostgreSQL and Redis service containers, import-linter, one migration head, pip-audit, secret scan, image build.
- Each of those checks required on `main`.

**Leaves out**

- Checks whose code comes in a later card join CI in that card: the access declaration test (F5), the error code text check (F4), Schemathesis (F12), the end-to-end smoke set (F11).
- The flow checks (F14) and the requirement-to-test report (F13).

**Done when**

Every backend check of 2.9 that has code to run; the checks are required on `main`

**Test cases**

Shown once on a throwaway pull request for each case.

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F2-1 | Main | A pull request with no problem | Every check passes, and each is required on `main` | M |
| F2-2 | Error | A lint error, a type error, or a failing test | The matching check fails | M |
| F2-3 | Error | A module imports another module's `repository.py` | import-linter fails | M |
| F2-4 | Error | A string shaped like a secret is committed | The secret scan fails | M |
| F2-5 | Error | A dependency with a known vulnerability | pip-audit fails | M |
| F2-6 | Error | Two migration heads | The migration check fails | M |

**Manual checks**

- [ ] F2-1: A pull request with no problem → Every check passes, and each is required on `main`
- [ ] F2-2: A lint error, a type error, or a failing test → The matching check fails
- [ ] F2-3: A module imports another module's `repository.py` → import-linter fails
- [ ] F2-4: A string shaped like a secret is committed → The secret scan fails
- [ ] F2-5: A dependency with a known vulnerability → pip-audit fails
- [ ] F2-6: Two migration heads → The migration check fails

## F14

**F14 Flow checks and agent roles**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 1 |
| Size | M |
| Risk | High: it adds the flow checks and the agent roles |
| Depends on | F2 |
| Requirements | Development flow |
| Spec sections | 2.12 |

**Scope**

- Failing first, test lock, mutation, and the risk gate as scripts with their own tests, in one reusable workflow that the other code repositories call with their own path list and test command.
- All four required on `main` in `tripmate-backend`.
- A surviving mutant counts as listed when the pull request description has a `## Surviving mutants` section with one line that names it and gives a reason; editing the description runs the check again.
- The backend path list for the High items of development-flow.md, section 2.1; the path list, the workflows, each `CLAUDE.md`, and the agent roles are on it; a path no rule names counts as High.
- On the backend path list, each module's `schemas.py`, `api.py`, and `events.py`, all of `app/platform/`, and `app/main.py`, `app/worker.py`, and `app/scheduler.py` are High.
- Failing first checks out each test commit and runs the tests that commit adds; a test commit that only changes existing tests, such as a correction after the code, is not asked to fail.
- A push to a pull request that has the label `human-approved` removes the label; the product owner adds it again after reading the new commits.
- The risk gate counts `human-approved` only when the label was last added after the newest commit reached the pull request, on every event.
- A commit without the trailer `Agent: test` that adds or changes any `conftest.py` breaks the test lock.
- The label `human-approved` exists.
- The four agent roles in `.claude/agents/` match development-flow.md, section 2.3: test, code, test-check, and diff review.

**Leaves out**

- Calling the workflow from the other repositories: CT1, WF2, MF2, DF1.

**Done when**

2.12: failing first, test lock, mutation, and the risk gate as scripts with their own tests, in one reusable workflow, required on `main`; the label `human-approved` exists

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F14-1 | Main | Every new test of the test commit fails at that commit | Failing first passes | U |
| F14-2 | Error | One new test of the test commit passes at that commit | Failing first fails and names the test | U |
| F14-3 | Error | A later commit with no `Agent: test` trailer edits, skips, or deletes a test of the test commit | Test lock fails and names the test | U |
| F14-4 | Edge | A later commit with the trailer changes that test | Test lock passes | U |
| F14-5 | Edge | A pull request with no test commit that changes only documents | Failing first and test lock pass | U |
| F14-6 | Main | A diff inside one module, on no listed path | The risk gate passes with no label | U |
| F14-7 | Error | A diff that touches a migration, with no label | The risk gate fails and names the path | U |
| F14-8 | Main | The same diff with the label `human-approved` | The risk gate passes | U |
| F14-9 | Edge | A diff that changes the path list, a workflow, a `CLAUDE.md`, or an agent role | The risk gate treats it as High | U |
| F14-10 | Edge | A diff on a path that no rule names | The risk gate treats it as High | U |
| F14-11 | Error | A changed rule file with a surviving mutant that the pull request does not list | The mutation check fails and shows the mutant | U |
| F14-12 | Main | The four role files in `.claude/agents/` | Each names what it reads, what it may change, and what it never does, as in development-flow.md, section 2.3 | M |

**Manual checks**

- [ ] F14-12: The four role files in `.claude/agents/` → Each names what it reads, what it may change, and what it never does, as in development-flow.md, section 2.3
- [ ] Checkpoint: On throwaway pull requests: a test that passes before its code, a locked test weakened by a later commit, and a migration with no label each fail CI. From here a Low card merges on its own.

## F3

**F3 Database session and migrations**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 2 |
| Size | S |
| Risk | High: it sets up migrations |
| Depends on | F1 |
| Requirements | Integrity |
| Spec sections | 2.2 (Transaction), 4.2 |

**Scope**

- One database transaction per request: the change is stored when the handler returns, and nothing is stored when it raises.
- Alembic set up with its first empty migration.
- Integration tests get a clean database for each test; test builders have their base.

**Leaves out**

- The `idempotency_keys` table (F7) and the `outbox_events` table (F8).
- Any module table.

**Done when**

One transaction per request; Alembic set up with its first empty migration; integration tests get a clean database; test builders have their base

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F3-1 | Main | A handler writes a row and returns | The row is stored | I |
| F3-2 | Error | A handler writes a row, then raises | No row is stored | I |
| F3-3 | Edge | Two tests run one after the other | The second sees none of the first one's rows | I |
| F3-4 | Main | An empty database | Migrating up to the head, then down to the base, both succeed | I |
| F3-5 | Edge | A model differs from the migrations | The migration check fails | I |

**Manual checks**

None.

## F13

**F13 Requirement-to-test report**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 2 |
| Size | S |
| Risk | High: it adds a CI check |
| Depends on | F2 |
| Requirements | Development flow |
| Spec sections | 2.12 (Requirement-to-test report) |

**Scope**

- The report built from test names and `tests/requirement-ids.txt`, kept with each CI run.
- A P0 ID in scope with no test, or whose only test is skipped, fails CI and names the ID.
- A test named with an ID that is not in the list fails CI and names the test.
- IDs of epics not in scope yet are not asked for.

**Leaves out**

- The IDs of each epic: the first card of that epic adds them.

**Done when**

2.12: the report from test names and `tests/requirement-ids.txt`, kept with each CI run; a P0 ID in scope with no test fails CI

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F13-1 | Main | Tests named with requirement IDs | The report lists each ID in scope with its tests | U |
| F13-2 | Error | A P0 requirement ID in scope with no test | The check fails and names the ID | U |
| F13-3 | Edge | A requirement ID of an epic that is not in scope yet | It is not asked for | U |
| F13-4 | Error | A test named with an ID that is not in the list | The check fails and names the test | U |
| F13-5 | Edge | The only test of an ID is skipped | The ID counts as having no test | U |

**Manual checks**

None.

## F4

**F4 Error format and error codes**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 3 |
| Size | S |
| Risk | High: it defines the error format of the contract |
| Depends on | F1, CT1 |
| Requirements | Input validation; Language tests |
| Spec sections | 2.3 |

**Scope**

- One exception type with a status, a code, `params`, and `fields`, turned into the format of api-design.md, section 4.1.
- A body that breaks its schema returns `400 request.invalid` with one entry in `fields` per problem.
- Anything unexpected returns `500 server.error` with the request ID and no detail.
- Every response carries `X-Request-Id`.
- A module registers the codes it can return; the error code text check fails when a registered code has no text in both languages in the pinned contracts version, and joins CI.
- The codes this epic adds (2.3), with their text in `tripmate-contracts`.

**Leaves out**

- Sending the full error to error reporting: F10.
- Codes of later epics.

**Done when**

2.3: one exception type, `request.invalid` with fields, `500 server.error`, request ID; registered codes checked for text in both languages

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F4-1 | Main | A module raises its error with a status, a code, and `params` | The response has that status and the format of api-design.md, section 4.1, with `X-Request-Id` equal to `requestId` | A |
| F4-2 | Error | A body that breaks its schema in two fields | `400 request.invalid` with two entries in `fields` | A |
| F4-3 | Error | A body that is not JSON | `400 request.invalid` | A |
| F4-4 | Error | A handler raises something unexpected | `500 server.error` with the request ID; the body holds no message and no stack | A |
| F4-5 | Edge | Two requests | Two different request IDs | A |
| F4-6 | Error | A registered code has no Vietnamese text in the pinned contracts version | The error code text check fails and names the code | C |
| F4-7 | Edge | A registered code has text in both languages | The check passes | C |

**Manual checks**

None.

## F8

**F8 Outbox**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 3 |
| Size | S |
| Risk | High: it adds the `outbox_events` migration |
| Depends on | F3 |
| Requirements | Integrity; Realtime |
| Spec sections | 2.5, 4.2 |

**Scope**

- The `outbox_events` migration.
- A module adds a domain event with one call inside its transaction; `trip_seq` is assigned in the same transaction, per trip, with no gap.
- The integrity test: a failed transaction leaves no outbox row and no record.

**Leaves out**

- Publishing the events: F9.
- The foreign key on `outbox_events.trip_id`: the EPIC-02 migration that creates `trips`.

**Done when**

The `outbox_events` migration; adding an event inside a transaction; the integrity test

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F8-1 | Main | A transaction adds one event and commits | One outbox row with the trip's next `trip_seq` and no `published_at` | I |
| F8-2 | Error | A transaction adds an event, then fails | No outbox row and no record | I |
| F8-3 | Edge | One transaction adds two events | Two rows with consecutive `trip_seq`, in the order added | I |
| F8-4 | Edge | Two transactions of the same trip commit at the same time | Their `trip_seq` values differ and leave no gap | I |
| F8-5 | Edge | Events of two trips | Each trip has its own numbering | I |

**Manual checks**

None.

## F12

**F12 Contract publishing**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 3 |
| Size | S |
| Risk | High: it changes how the contract is published and reads `CONTRACTS_TOKEN` |
| Depends on | F2, CT1 |
| Requirements | Cross-client API contract tests |
| Spec sections | 2.10 |

**Scope**

- `openapi.json` generated from the app, the same file every time for the same app.
- A stub route marked `x-stub: true` and returning `501`; every response schema of a stub has an example, or the check fails and names the operation.
- Schemathesis against the contract joins CI.
- A job that runs only on pushes to `main` opens a pull request in `tripmate-contracts` with the new file, using `CONTRACTS_TOKEN`; no pull request when the contract did not change.

**Leaves out**

- The draft and release tags, and the breaking-change check: CT2.
- Creating the token: the product owner (5.1).

**Done when**

2.10: `openapi.json` generated from the app, `x-stub` on stub routes, examples required on stubs, the pull request in `tripmate-contracts` with `CONTRACTS_TOKEN`, from a job that runs only on pushes to `main`

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F12-1 | Main | The app with no change | Generating `openapi.json` twice gives the same file | C |
| F12-2 | Main | A stub route | The contract marks it `x-stub: true`, and calling it returns `501` | A, C |
| F12-3 | Error | A stub response schema with no example | The check fails and names the operation | C |
| F12-4 | Main | A merge to `main` that changes the contract | A pull request opens in `tripmate-contracts` with the new file | M |
| F12-5 | Edge | A merge to `main` that does not change the contract | No pull request | M |
| F12-6 | Edge | A pull request from a branch that edits the publishing workflow | The publishing job does not run, so the token is never read | M |

**Manual checks**

- [ ] F12-4: A merge to `main` that changes the contract → A pull request opens in `tripmate-contracts` with the new file
- [ ] F12-5: A merge to `main` that does not change the contract → No pull request
- [ ] F12-6: A pull request from a branch that edits the publishing workflow → The publishing job does not run, so the token is never read

## F5

**F5 Access declaration and token check**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 4 |
| Size | M |
| Risk | High: it builds the access guard and the token check |
| Depends on | F4 |
| Requirements | Business rules 3 and 4, access |
| Spec sections | 2.2 |

**Scope**

- Every route declares `public`, `signed_in`, or a capability; the declaration test fails CI for a route with none and names it.
- `X-Device-Id` required on every request except the health check; missing or not a UUID returns `400 request.invalid`.
- For a route that is not `public`: the EdDSA access token signature and expiry are checked with test keys, and `identity` is asked through its registered hook whether the session is active.
- Failures return `401 auth.not_signed_in` or `401 auth.token_expired`.
- A `signed_in` test route for the checkpoint.

**Leaves out**

- Capability routes: the guard asks `trips` for membership from EPIC-02.
- Issuing tokens, sessions, and the real `identity` hook: EPIC-01.

**Done when**

2.2: every route declares its access; the declaration test; device ID required; EdDSA token check with test keys; the session hook for `identity`

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F5-1 | Error | A route with no access declaration | The declaration test fails and names the route | A |
| F5-2 | Main | A `signed_in` route, a valid token, an active session | `200`, and the handler knows the user ID and session ID | A |
| F5-3 | Main | A `public` route with no token | `200` | A |
| F5-4 | Error | A `signed_in` route with no `Authorization` header | `401 auth.not_signed_in` | A |
| F5-5 | Error | A token past its expiry | `401 auth.token_expired` | A |
| F5-6 | Error | A token signed with another key, or with the algorithm `none` | `401 auth.not_signed_in` | A |
| F5-7 | Edge | A valid token whose session the hook reports as ended | `401 auth.token_expired` | A |
| F5-8 | Edge | A token that expires one second from now, and one that expired one second ago | `200`, then `401 auth.token_expired` | A |
| F5-9 | Error | No `X-Device-Id`, or one that is not a UUID | `400 request.invalid` | A |
| F5-10 | Edge | The health check with no `X-Device-Id` | `200` | A |

**Manual checks**

- [ ] Checkpoint: A `signed_in` test route answers with each error of 2.3, and removing its declaration fails CI.

## F6

**F6 Rate limits**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 4 |
| Size | S |
| Risk | Low: nothing on the High list: one part of `platform`, no table, no contract change |
| Depends on | F5 |
| Requirements | -- |
| Spec sections | 2.2 (Rate limits) |

**Scope**

- The general ceiling of security.md, section 2.2, on every route; a route can add its own limit with its own key.
- Counters in Redis; over the limit returns `429 rate.limited` with `Retry-After` set to the seconds left in the window.
- When Redis does not answer, a rate-limited route returns `503 server.unavailable`; the request is not let through.

**Leaves out**

- The limits of each EPIC-01 route (sign-in, sign-up): EPIC-01 sets them with this mechanism.

**Done when**

The general ceiling and per-route limits in Redis; `429 rate.limited` with `Retry-After`

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F6-1 | Main | Requests up to the limit | Each one passes | A |
| F6-2 | Error | One request over the limit | `429 rate.limited`, with `Retry-After` set to the seconds left in the window | A |
| F6-3 | Edge | The window has passed | The next request passes | A |
| F6-4 | Edge | Two callers with different keys | Each is counted on its own | A |
| F6-5 | Main | A route with its own limit and key | Its limit applies as well as the general ceiling | A |
| F6-6 | Edge | Redis does not answer | `503 server.unavailable`; the request is not let through | I |

**Manual checks**

None.

## F7

**F7 Idempotency keys**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 4 |
| Size | M |
| Risk | High: it adds the `idempotency_keys` migration |
| Depends on | F3, F5 |
| Requirements | Idempotency |
| Spec sections | 2.4, 4.2 |

**Scope**

- The `idempotency_keys` migration, with `device_id` next to `user_id`.
- Every case of 2.4: a new key runs and stores the response; the same key and body returns it; a different body returns `409 request.idempotency_mismatch`; a key in flight returns `409 request.idempotency_in_progress`; a `5xx` stores nothing; a `4xx` is stored.
- A create `POST` with no key returns `400 request.idempotency_key_required`; a key that is not a UUID returns `400 request.invalid`.
- A key belongs to the signed-in user, or before sign-in to the device ID.

**Leaves out**

- Deleting old keys: F9 cleanup.

**Done when**

The `idempotency_keys` migration; every case of 2.4 as a test

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F7-1 | Main | A create `POST` with a new key | The request runs once; its status and body are stored | I, A |
| F7-2 | Main | The same key and the same body again | The stored response; no second record | I, A |
| F7-3 | Error | The same key with a different body | `409 request.idempotency_mismatch` | A |
| F7-4 | Edge | The same key while the first request still runs | `409 request.idempotency_in_progress` | I |
| F7-5 | Edge | The first request fails with `5xx` | Nothing is stored; the retry runs | I |
| F7-6 | Edge | The first request is refused with `4xx` | The refusal is stored and returned for the same key and body | I |
| F7-7 | Error | A create `POST` with no key | `400 request.idempotency_key_required` | A |
| F7-8 | Error | A key that is not a UUID | `400 request.invalid` | A |
| F7-9 | Edge | Two users send the same key | Each request runs; neither sees the other's response | I |
| F7-10 | Edge | Before sign-in, two device IDs send the same key | Each request runs | I |

**Manual checks**

None.

## F10

**F10 Logs, traces, metrics, and error reporting**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 4 |
| Size | M |
| Risk | High: it reads the secrets of Sentry and Grafana Cloud |
| Depends on | F4 |
| Requirements | Data protection |
| Spec sections | 2.6 |

**Scope**

- JSON log lines with request ID, user ID, and trip ID, and no email, name, token, or request body.
- OpenTelemetry traces across the API, the worker, PostgreSQL, Redis, and outgoing HTTP calls, sent to Grafana Cloud.
- Metrics: request rate, errors, and latency per endpoint; queue depth and job age; outbox rows waiting.
- Error reporting to Sentry with the release tag and the request ID.
- A request still succeeds when Sentry or the trace collector does not answer.

**Leaves out**

- The alerts in production: F11.
- Error reporting on Web and Mobile: their own cards later.

**Done when**

2.6, sent to Sentry and Grafana Cloud (5.1); the log line test

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F10-1 | Main | Any request | One JSON log line with the request ID, and the user ID when signed in | U |
| F10-2 | Error | A sign-in request with an email, a password, and a token | The log line holds none of the three | U |
| F10-3 | Main | A handler raises something unexpected | Error reporting receives it with the release tag and the request ID | I |
| F10-4 | Main | A request that reads the database | One trace with a span for the request and one for the query | I |
| F10-5 | Edge | Error reporting or the trace collector does not answer | The request still succeeds | I |
| F10-6 | Main | The metrics endpoint or exporter | It carries request rate, errors, latency, queue depth, job age, and outbox rows waiting | I |

**Manual checks**

None.

## F9

**F9 Worker, scheduler, and cleanup**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 5 |
| Size | M |
| Risk | High: it adds the worker and the scheduler, which run in production |
| Depends on | F7, F8 |
| Requirements | Realtime; Background work |
| Spec sections | 2.5 |

**Scope**

- Taskiq on Redis.
- The outbox publisher: new events in `id` order to the trip's Redis channel, `published_at` set, a few seconds after each commit and as a sweep every few seconds.
- Handlers registered by other modules run as jobs, one per event and handler, and are idempotent.
- The scheduler wakes every minute, runs each registered due-work query, and enqueues what is due; it keeps no timers of its own.
- Daily cleanup: idempotency keys after 24 hours, published outbox rows after 7 days.

**Leaves out**

- The WebSocket gateway that reads the channels: the first epic that sends an event to a screen (5.1).

**Done when**

2.5: Taskiq on Redis, the outbox publisher, event handlers as jobs, the scheduler's due-work queries, daily cleanup; the background work tests

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F9-1 | Main | A committed event | It is published to its trip's channel, and `published_at` is set | I |
| F9-2 | Edge | Three events waiting | They are published in `id` order | I |
| F9-3 | Edge | The publisher runs again after publishing | Nothing is published twice | I |
| F9-4 | Error | Redis does not answer while publishing | `published_at` stays empty; the next sweep publishes the event | I |
| F9-5 | Main | An event with two registered handlers | One job per handler | I |
| F9-6 | Edge | A handler's job runs twice for one event | The second run changes nothing | I |
| F9-7 | Main | A due-work query returns one item that is due | The scheduler enqueues it at its next tick | I |
| F9-8 | Edge | The scheduler restarts while work is due | The work is enqueued after the restart | I |
| F9-9 | Main | Idempotency keys of 25 hours and of 23 hours | Cleanup deletes the first and keeps the second | I |
| F9-10 | Edge | A published outbox row of 8 days, and an unpublished one of 8 days | Cleanup deletes the first and keeps the second | I |

**Manual checks**

None.

## F11

**F11 Local stack, image, and production on Render**

**Facts**

| Fact | Value |
|------|-------|
| Epic | EPIC-11 |
| Repository | `tripmate-backend` |
| Machine | Mac 1, wave 5 |
| Size | M |
| Risk | High: it changes hosting, the image, and how secrets are read |
| Depends on | F9, F10 |
| Requirements | Performance |
| Spec sections | 2.8, 2.6 (Alerts) |

**Scope**

- First: check that Render's free Postgres plan can hold the data until the beta; if it cannot, stop and ask the product owner.
- `compose.yaml` runs the whole stack (API, worker, scheduler from the image, PostgreSQL, Redis) with seed data; the end-to-end smoke set runs against it in CI.
- The image published to GitHub Container Registry for every release tag.
- `render.yaml` for production until the beta: one web instance on the free plan running the three processes, free Postgres and Key Value plans, migrations as the pre-deploy step.
- The alerts of 2.6 in Grafana Cloud: P95 latency over 500 ms, error rate, job age over 5 minutes, outbox not draining.

**Leaves out**

- Staging and the full production setup: before the beta, Phase 7.
- The domain: bought before the beta.
- The Web static site: WF3.

**Done when**

2.8: `compose.yaml` runs the whole stack with seed data; the image is published for every release tag; `render.yaml` for production until the beta (one instance, free database plans) with migrations as the pre-deploy step; the alerts of 2.6

**Test cases**

| Case | Kind | Given | Expected | Level |
|------|------|-------|----------|-------|
| F11-1 | Main | `docker compose up` on a clean machine, in CI | The health check returns `200` and the seed data is present | E |
| F11-2 | Main | A release tag | The image for that tag is in GitHub Container Registry | M |
| F11-3 | Main | Production after a deploy | The health check returns `200` with the release version; the alerts of 2.6 exist | M |
| F11-4 | Error | A deploy whose migration fails | The deploy stops; the previous version keeps serving | M |
| F11-5 | Edge | A rollback | Redeploying the previous image restores the previous version | M |
| F11-6 | Main | The one production instance | The API answers, a job enqueued by a request runs, and the scheduler ticks | M |

**Manual checks**

- [ ] F11-2: A release tag → The image for that tag is in GitHub Container Registry
- [ ] F11-3: Production after a deploy → The health check returns `200` with the release version; the alerts of 2.6 exist
- [ ] F11-4: A deploy whose migration fails → The deploy stops; the previous version keeps serving
- [ ] F11-5: A rollback → Redeploying the previous image restores the previous version
- [ ] F11-6: The one production instance → The API answers, a job enqueued by a request runs, and the scheduler ticks
- [ ] Checkpoint: `docker compose up` runs the whole stack, and the health check answers locally and in production.

## Version History

| Version | Date | Changes |
|---------|------|---------|
| 1.0 | 2026-11-25 | First version: the 14 tasks of EPIC-11 in this repository, moved from the EPIC-11 spec, sections 6 and 7, with scope, what each leaves out, the reason for its risk level, and its manual checks. The skeleton tasks write four agent roles, as development-flow.md now says. |
| 1.1 | 2026-11-25 | F14 manual checks: F14-12 names the four role files, as its test case does. |
| 1.2 | 2026-11-26 | F14 scope: where a pull request lists a surviving mutant, a `## Surviving mutants` section of its description, as the product owner decided. |
| 1.3 | 2026-11-26 | F14 scope, as the product owner decided after review round 1: the contract, access, and platform files are High on the backend path list; failing first checks each test commit; a push removes `human-approved`. |
| 1.4 | 2026-11-26 | F14 scope, as the product owner decided after review round 2: the label counts only when added after the newest commit; any `conftest.py` change outside a test commit breaks the test lock. |
