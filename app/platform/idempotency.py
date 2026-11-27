"""Idempotency keys for create requests (EPIC-11 spec, section 2.4; data-model.md, section 6.3).

A route declares it takes a key with the dependency `idempotent`, next to its access
declaration: `dependencies=[Depends(public), Depends(idempotent)]`.

- The dependency checks the `Idempotency-Key` header (missing: `400
  request.idempotency_key_required`; not a UUID: `400 request.invalid`) and claims the key in a
  transaction of its own, before the handler runs: the row is in flight (no `status`, no
  `completed_at`). The key belongs to the signed-in user (a `signed_in` route) or, before
  sign-in, to the device ID. What an earlier row for the key means is decided by `judge`: expired
  (older than 24 hours) or abandoned (in flight for more than 60 seconds) rows are taken over, a
  row for another request (method, path, body) is `409 request.idempotency_mismatch`, one in
  flight is `409 request.idempotency_in_progress`, and a finished one is replayed without running
  the handler (`Idempotent-Replayed: true`).
- `IdempotencyMiddleware` sees the whole response, so it ends the request. While a key is
  claimed, `get_session` hands the handler's open transaction over (`SessionHandoff`) instead of
  committing it, so a `2xx` is stored in the handler's transaction and both commit together or
  not at all; a commit that fails answers `500` and leaves no row. A `4xx` the handler answers
  rolls its transaction back and is stored in a transaction of its own. A `5xx` deletes the row.
  A request refused before the handler (`400` for its schema, and `401`, `429`, `503` from
  access or rate limits, which never claim the key) stores nothing and releases the key; a `401`
  or `429` the handler itself answers is stored like any `4xx`. Only the status, the body, and
  the headers `Location`, `ETag`, and `Content-Type` are stored.
- "The same request" is the SHA-256 of the method, the path, and the body: a JSON body with its
  keys sorted and no whitespace, any other body as its bytes.
- The contract documents the required header and the `400` and `409` answers on every operation
  that declares `idempotent` (`install_idempotency_contract`).

The deletion of old rows is card F9's.
"""

from __future__ import annotations

import enum
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any, cast

import anyio
from fastapi import FastAPI, Request
from fastapi.dependencies.models import Dependant
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute, iter_route_contexts
from sqlalchemy import CursorResult, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.platform.access import (
    HEALTH_PATH,
    checked_caller,
    public,
    route_needs_signed_in,
    signed_in,
)
from app.platform.db import HANDOFF_KEY, SessionHandoff
from app.platform.errors import (
    FIELD_INVALID,
    IDEMPOTENCY_IN_PROGRESS,
    IDEMPOTENCY_KEY_REQUIRED,
    IDEMPOTENCY_MISMATCH,
    REQUEST_INVALID,
    SCHEMA_REFUSED_KEY,
    SERVER_ERROR,
    AppError,
    error_body,
)

logger = logging.getLogger(__name__)

KEY_HEADER = "Idempotency-Key"
REPLAYED_HEADER = "Idempotent-Replayed"
KEY_PARAMETER = "IdempotencyKey"
DEVICE_ID_HEADER = "x-device-id"
# The headers of a response that are stored and replayed (lower case, as in ASGI).
STORED_HEADERS = ("location", "etag", "content-type", "retry-after")
# A response held back for storing is at most this long; a longer one is passed through unstored.
MAX_STORED_RESPONSE_BYTES = 1024 * 1024
# A key in flight for longer than this has no live request behind it.
ABANDONED_AFTER_SECONDS = 60
# A key older than this is expired, whatever its state.
EXPIRED_AFTER_SECONDS = 24 * 60 * 60
# Private to the platform: the claim of this request.
_CLAIM_KEY = "_tripmate_platform_idempotency_claim"
_CLAIM_ATTEMPTS = 3


# -- The rules (pure) ----------------------------------------------------------------------


def parse_key(value: str) -> uuid.UUID | None:
    """The key as a UUID when `value` is one in its usual form (hyphens), else `None`."""
    try:
        key = uuid.UUID(value)
    except ValueError:
        return None
    return key if str(key) == value.lower() else None


def _is_json(content_type: str) -> bool:
    media = content_type.split(";")[0].strip().lower()
    # With no content type FastAPI still parses the body as JSON, so it is hashed as JSON.
    return media == "" or media == "application/json" or media.endswith("+json")


def request_hash(method: str, path: str, content_type: str, body: bytes) -> str:
    """SHA-256 of the method, the path, and the body (a JSON body as sorted, compact JSON)."""
    canonical = body
    if body and _is_json(content_type):
        try:
            canonical = json.dumps(
                json.loads(body), sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
        except ValueError:  # not JSON after all: its bytes
            canonical = body
    digest = hashlib.sha256()
    for part in (method.upper().encode(), path.encode(), canonical):
        digest.update(len(part).to_bytes(8, "big"))
        digest.update(part)
    return digest.hexdigest()


class Verdict(enum.Enum):
    """What an earlier row for the key means for this request."""

    TAKE_OVER = "take_over"  # expired or abandoned: run, and replace the row
    MISMATCH = "mismatch"  # the key was used for another request
    IN_PROGRESS = "in_progress"
    REPLAY = "replay"


def judge(*, status: int | None, stored_hash: str, age_seconds: float, new_hash: str) -> Verdict:
    """Decide on an existing row: `status` is `None` while its request runs."""
    if age_seconds > EXPIRED_AFTER_SECONDS:
        return Verdict.TAKE_OVER
    if status is None and age_seconds > ABANDONED_AFTER_SECONDS:
        return Verdict.TAKE_OVER
    if stored_hash != new_hash:
        return Verdict.MISMATCH
    return Verdict.IN_PROGRESS if status is None else Verdict.REPLAY


def stored_headers(raw: list[tuple[bytes, bytes]]) -> dict[str, str]:
    """The headers of a response that are stored: `Location`, `ETag`, `Content-Type`."""
    found: dict[str, str] = {}
    for name, value in raw:
        lowered = name.decode("latin-1").lower()
        if lowered in STORED_HEADERS:
            found[lowered] = value.decode("latin-1")
    return found


# -- The table -----------------------------------------------------------------------------

_INSERT = text(
    "INSERT INTO idempotency_keys (key, user_id, device_id, method, path, request_hash) "
    "VALUES (:key, :user_id, :device_id, :method, :path, :hash) "
    "ON CONFLICT DO NOTHING RETURNING id, created_at"
)
_SELECT = (
    "SELECT id, status, request_hash, response_headers, response_body, "
    "extract(epoch FROM clock_timestamp() - created_at) AS age "
    "FROM idempotency_keys WHERE key = :key AND {owner} FOR UPDATE"
)
_OWNERS = {True: "user_id = :user_id", False: "device_id = :device_id"}
_TAKE_OVER = text(
    "UPDATE idempotency_keys SET method = :method, path = :path, request_hash = :hash, "
    "status = NULL, response_headers = '{}'::jsonb, response_body = NULL, "
    "created_at = clock_timestamp(), completed_at = NULL WHERE id = :id "
    "RETURNING id, created_at"
)
_STORE = text(
    "UPDATE idempotency_keys SET status = :status, response_headers = CAST(:headers AS jsonb), "
    "response_body = :body, completed_at = clock_timestamp() "
    "WHERE id = :id AND created_at = :created_at"
)
_IS_STORED = text(
    "SELECT 1 FROM idempotency_keys "
    "WHERE id = :id AND created_at = :created_at AND status IS NOT NULL"
)
_DELETE = text("DELETE FROM idempotency_keys WHERE id = :id AND created_at = :created_at")

SessionFactory = async_sessionmaker[AsyncSession]


@dataclass(frozen=True)
class Stored:
    """A stored response."""

    status: int
    headers: dict[str, str]
    body: bytes


class Replay(Exception):  # noqa: N818 (not an error: it carries the answer)
    """Raised by `idempotent` to answer with the stored response, the handler not running."""

    def __init__(self, stored: Stored) -> None:
        super().__init__("idempotent replay")
        self.stored = stored


@dataclass
class Claim:
    """The in-flight row this request owns, and the hand-over of its transaction."""

    id: int
    created_at: Any
    handoff: SessionHandoff


async def _claim(
    factory: SessionFactory,
    *,
    key: uuid.UUID,
    user_id: uuid.UUID | None,
    device_id: uuid.UUID | None,
    method: str,
    path: str,
    new_hash: str,
) -> tuple[int, Any]:
    """Write the key in flight in a transaction of its own; returns the row's `(id, created_at)`.

    Raises `Replay`, or `AppError` `409`, when an earlier row decides otherwise.
    """
    params = {
        "key": key,
        "user_id": user_id,
        "device_id": device_id,
        "method": method,
        "path": path,
        "hash": new_hash,
    }
    select = text(_SELECT.format(owner=_OWNERS[user_id is not None]))
    for _ in range(_CLAIM_ATTEMPTS):
        async with factory() as session, session.begin():
            inserted = (await session.execute(_INSERT, params)).first()
            if inserted is not None:
                return int(inserted.id), inserted.created_at
            row = (await session.execute(select, params)).first()
            if row is None:  # deleted since the conflict: try again
                continue
            verdict = judge(
                status=row.status,
                stored_hash=row.request_hash,
                age_seconds=float(row.age),
                new_hash=new_hash,
            )
            if verdict is Verdict.TAKE_OVER:
                taken = (await session.execute(_TAKE_OVER, {**params, "id": row.id})).one()
                return int(taken.id), taken.created_at
            if verdict is Verdict.MISMATCH:
                raise AppError(409, IDEMPOTENCY_MISMATCH)
            if verdict is Verdict.IN_PROGRESS:
                raise AppError(409, IDEMPOTENCY_IN_PROGRESS)
            headers = row.response_headers
            raise Replay(
                Stored(
                    status=int(row.status),
                    headers=json.loads(headers) if isinstance(headers, str) else dict(headers),
                    body=bytes(row.response_body or b""),
                )
            )
    raise AppError(409, IDEMPOTENCY_IN_PROGRESS)


def _store_params(
    claim: Claim, status: int, headers: dict[str, str], body: bytes
) -> dict[str, Any]:
    return {
        "id": claim.id,
        "created_at": claim.created_at,
        "status": status,
        "headers": json.dumps(headers),
        "body": body,
    }


async def _store_in_session(
    session: AsyncSession, claim: Claim, status: int, headers: dict[str, str], body: bytes
) -> bool:
    """Write the response; `False` when the row is no longer this request's (taken over)."""
    result = cast(
        "CursorResult[Any]",
        await session.execute(_STORE, _store_params(claim, status, headers, body)),
    )
    return result.rowcount == 1


async def _store_alone(
    factory: SessionFactory, claim: Claim, status: int, headers: dict[str, str], body: bytes
) -> bool:
    """Write the response in a transaction of its own; `False` when the claim was taken over."""
    async with factory() as session, session.begin():
        return await _store_in_session(session, claim, status, headers, body)


async def _is_stored(factory: SessionFactory, claim: Claim) -> bool:
    """Whether the claim's row holds a response (after a commit whose outcome is unknown)."""
    async with factory() as session, session.begin():
        row = (
            await session.execute(_IS_STORED, {"id": claim.id, "created_at": claim.created_at})
        ).first()
    return row is not None


async def _delete(factory: SessionFactory, claim: Claim) -> None:
    async with factory() as session, session.begin():
        await session.execute(_DELETE, {"id": claim.id, "created_at": claim.created_at})


# -- The declaration -----------------------------------------------------------------------


async def idempotent(request: Request) -> None:
    """Declares that a route takes an `Idempotency-Key`; claims the key before the handler runs.

    It is not an access declaration: the route still declares `public`, `signed_in`, or a
    capability.
    """
    value = request.headers.get(KEY_HEADER)
    if not value:
        raise AppError(400, IDEMPOTENCY_KEY_REQUIRED)
    key = parse_key(value)
    if key is None:
        raise AppError(400, REQUEST_INVALID, fields=[{"field": KEY_HEADER, "code": FIELD_INVALID}])
    user_id: uuid.UUID | None = None
    device_id: uuid.UUID | None = None
    if route_needs_signed_in(request):
        caller = checked_caller(request) or await signed_in(request)
        user_id = caller.user_id
    else:  # before sign-in; the device ID was checked by its middleware
        device_id = uuid.UUID(request.headers[DEVICE_ID_HEADER])
    path = request.url.path
    new_hash = request_hash(
        request.method, path, request.headers.get("content-type", ""), await request.body()
    )
    factory: SessionFactory = request.app.state.session_factory
    row_id, created_at = await _claim(
        factory,
        key=key,
        user_id=user_id,
        device_id=device_id,
        method=request.method.upper(),
        path=path,
        new_hash=new_hash,
    )
    claim = Claim(row_id, created_at, SessionHandoff())
    state = request.scope.setdefault("state", {})
    state[_CLAIM_KEY] = claim
    state[HANDOFF_KEY] = claim.handoff


async def _replay_handler(request: Request, error: Exception) -> Response:
    if not isinstance(error, Replay):  # registered for it only
        raise error
    stored = error.stored
    headers = {**stored.headers, REPLAYED_HEADER: "true"}
    return Response(content=stored.body, status_code=stored.status, headers=headers)


# -- The middleware ------------------------------------------------------------------------


def _claim_of(scope: Scope) -> Claim | None:
    claim = scope.get("state", {}).get(_CLAIM_KEY)
    return claim if isinstance(claim, Claim) else None


class IdempotencyMiddleware:
    """Ends the request of a claimed key once its whole response is known (see the module).

    Pure ASGI. A request that claimed no key (no `idempotent`, or refused by it) passes through
    untouched; a claimed one has its response held back until it is stored or released.
    It sits inside the request ID middleware, whose `X-Request-Id` the answers it builds get.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        start: Message | None = None
        body = bytearray()
        ended = False  # the claim is stored, deleted, or taken over: nothing left to release
        dropping = False  # the answer was replaced: the app's later messages are dropped

        async def hold(message: Message) -> None:
            nonlocal start, ended, dropping
            if dropping:
                return
            claim = _claim_of(scope)
            kind = message["type"]
            if kind == "http.response.start" and claim is not None and not ended:
                start = message
            elif start is None or claim is None or ended:
                await send(message)
            elif kind == "http.response.body":
                body.extend(message.get("body", b""))
                more = bool(message.get("more_body", False))
                if len(body) > MAX_STORED_RESPONSE_BYTES:  # too long to hold: not stored
                    ended = True
                    with anyio.CancelScope(shield=True):
                        dropping = not await self._pass_through(scope, receive, send, claim, start)
                    if not dropping:
                        await send({**message, "body": bytes(body)})
                elif not more:
                    ended = True
                    # A client that disconnects must not stop the store or the commit halfway.
                    with anyio.CancelScope(shield=True):
                        await self._end(scope, receive, send, claim, start, bytes(body))
            else:  # `http.response.pathsend` and the like: the body is not in the message
                ended = True
                with anyio.CancelScope(shield=True):
                    dropping = not await self._pass_through(scope, receive, send, claim, start)
                if not dropping:
                    if body:
                        await send(
                            {"type": "http.response.body", "body": bytes(body), "more_body": True}
                        )
                    await send(message)

        try:
            await self.app(scope, receive, hold)
        finally:
            # The app ended without its whole response (a client that disconnected, or an error):
            # give the key and the transaction back.
            claim = _claim_of(scope)
            if claim is not None and not ended:
                with anyio.CancelScope(shield=True):
                    await self._release(scope, claim)

    @staticmethod
    def _factory(scope: Scope) -> SessionFactory:
        factory: SessionFactory = scope["app"].state.session_factory
        return factory

    async def _release(self, scope: Scope, claim: Claim) -> None:
        """Drop the row (best effort: an abandoned row frees the key after 60 seconds)."""
        try:
            await claim.handoff.rollback()
            await _delete(self._factory(scope), claim)
        except Exception:
            logger.exception("Could not release idempotency key row %s", claim.id)

    async def _pass_through(
        self, scope: Scope, receive: Receive, send: Send, claim: Claim, start: Message
    ) -> bool:
        """End the claim without storing the response, then send its start.

        A `2xx`/`3xx` still commits the handler's transaction; the row is deleted so the key is
        free. `False` when it failed: a `500` was sent instead and the app's messages are dropped.
        """
        refused = bool(scope.get("state", {}).get(SCHEMA_REFUSED_KEY))
        try:
            if start["status"] < 400 and not refused:
                await claim.handoff.commit()
            else:
                await claim.handoff.rollback()
            await _delete(self._factory(scope), claim)
        except Exception:
            logger.exception("Could not end idempotency key row %s", claim.id)
            await self._release(scope, claim)
            await self._answer(scope, receive, send, AppError(500, SERVER_ERROR))
            return False
        await send(start)
        return True

    async def _end(
        self, scope: Scope, receive: Receive, send: Send, claim: Claim, start: Message, body: bytes
    ) -> None:
        status: int = start["status"]
        factory = self._factory(scope)
        refused = bool(scope.get("state", {}).get(SCHEMA_REFUSED_KEY))
        try:
            if status >= 500 or refused:
                await claim.handoff.rollback()
                await _delete(factory, claim)
            elif status >= 400:  # the handler's own refusal, stored after its changes are undone
                await claim.handoff.rollback()
                headers = stored_headers(start["headers"])
                if not await _store_alone(factory, claim, status, headers, body):
                    await self._answer(scope, receive, send, AppError(409, IDEMPOTENCY_IN_PROGRESS))
                    return
            elif not await self._store_success(factory, claim, start, body):
                await self._answer(scope, receive, send, AppError(409, IDEMPOTENCY_IN_PROGRESS))
                return
        except Exception:
            logger.exception("Could not end idempotency key row %s", claim.id)
            await self._release(scope, claim)
            await self._answer(scope, receive, send, AppError(500, SERVER_ERROR))
            return
        await send(start)
        await send({"type": "http.response.body", "body": body})

    @staticmethod
    async def _store_success(
        factory: SessionFactory, claim: Claim, start: Message, body: bytes
    ) -> bool:
        """Store a `2xx`/`3xx` in the handler's transaction and commit both; `False` when the
        row was taken over meanwhile (the handler's work is rolled back then)."""
        headers = stored_headers(start["headers"])
        session = claim.handoff.session
        if session is None:  # the handler used no session: nothing to commit with it
            return await _store_alone(factory, claim, start["status"], headers, body)
        if not await _store_in_session(session, claim, start["status"], headers, body):
            await claim.handoff.rollback()
            return False
        try:
            await claim.handoff.commit()
        except Exception:
            # The outcome may be unknown (the connection broke after the commit was sent): the
            # row says. Kept when it holds the response, else the caller deletes it.
            if await _is_stored(factory, claim):
                logger.warning(
                    "Commit of idempotency key row %s failed but its response is stored", claim.id
                )
                return True
            raise
        return True

    @staticmethod
    async def _answer(scope: Scope, receive: Receive, send: Send, error: AppError) -> None:
        request_id = scope.get("state", {}).get("request_id")
        content = error_body(error, request_id if isinstance(request_id, str) else None)
        await JSONResponse(status_code=error.status, content=content)(scope, receive, send)


def install_idempotency(app: FastAPI) -> None:
    """Install the replay answer and the middleware (inside the device ID and request ID ones)."""
    app.add_exception_handler(Replay, _replay_handler)
    app.add_middleware(IdempotencyMiddleware)


# -- The order of the declaration -----------------------------------------------------------


def _allowed_after_idempotent(call: Any) -> bool:
    """What may follow `idempotent` in a route's dependencies: it never answers a `4xx` there.

    `public` checks nothing; `signed_in` was checked before (the general ceiling), and a route's
    own rate limit was counted with the ceiling. Any other dependency (a capability, a lookup)
    must come first, or its refusal would be stored as the handler's.
    """
    return call is public or call is signed_in or hasattr(call, "route_limit")


def misplaced_idempotent(app: FastAPI) -> list[str]:
    """Each route (`METHOD /path`) with a dependency after `idempotent` that may refuse."""
    found: list[str] = []
    for context in iter_route_contexts(app.routes):
        route = context.original_route
        if not isinstance(route, APIRoute):
            continue
        calls = [depends.dependency for depends in route.dependencies]
        if idempotent not in calls:
            continue
        after = calls[calls.index(idempotent) + 1 :]
        if any(not _allowed_after_idempotent(call) for call in after):
            found.extend(f"{method} {context.path}" for method in sorted(context.methods or ()))
    return sorted(found)


def check_idempotent_order(app: FastAPI) -> None:
    """Fail at start-up when `idempotent` is not the last dependency of a route that declares it.

    The key is claimed where `idempotent` runs; a `4xx` raised by a dependency after it would be
    stored as the handler's answer (spec 2.4), so such a dependency must be declared before it.
    """
    misplaced = misplaced_idempotent(app)
    if misplaced:
        raise RuntimeError(
            "Depends(idempotent) must be the last of a route's dependencies (a refusal after it "
            "is stored and replayed); declare it after the access and guard dependencies of: "
            + ", ".join(misplaced)
        )


# -- The contract --------------------------------------------------------------------------


def _declares(dependant: Dependant) -> bool:
    return any(
        dependency.call is idempotent or _declares(dependency)
        for dependency in dependant.dependencies
    )


def _idempotent_operations(app: FastAPI) -> set[tuple[str, str]]:
    """`(method, path)` of each route that declares `idempotent` (path as in the contract)."""
    return {
        (method.lower(), context.path)
        for context in iter_route_contexts(app.routes)
        if isinstance(context.original_route, APIRoute)
        and context.path is not None
        and _declares(context.original_route.dependant)
        for method in context.methods or ()
    }


def _error_response(description: str) -> dict[str, Any]:
    return {
        "description": description,
        "content": {"application/json": {"schema": {"$ref": "#/components/schemas/ErrorResponse"}}},
    }


IDEMPOTENCY_409_TEXT = "The key was used for another request, or its request is still running"


def _document_409(responses: dict[str, Any]) -> None:
    """Add the idempotency `409` to an operation, keeping the route's own description and
    content (its examples)."""
    existing = responses.get("409")
    if existing is None:
        responses["409"] = _error_response(IDEMPOTENCY_409_TEXT)
        return
    description = existing.get("description")
    if not description:
        existing["description"] = IDEMPOTENCY_409_TEXT
    elif IDEMPOTENCY_409_TEXT not in description:
        existing["description"] = f"{description}. {IDEMPOTENCY_409_TEXT}"
    content = existing.setdefault("content", {}).setdefault("application/json", {})
    content.setdefault("schema", {"$ref": "#/components/schemas/ErrorResponse"})


def install_idempotency_contract(app: FastAPI) -> None:
    """Declare in the contract, on every operation that declares `idempotent`, the required
    header `Idempotency-Key` (a UUID) and the answers `400` and `409` (`ErrorResponse`).

    Install it after `install_openapi`, whose `ErrorResponse` it refers to.
    """
    documented = app.openapi

    def openapi() -> dict[str, Any]:
        schema = documented()
        parameters = schema["components"].setdefault("parameters", {})
        if KEY_PARAMETER in parameters:
            return schema  # already done: the schema is cached and changed in place
        parameters[KEY_PARAMETER] = {
            "name": KEY_HEADER,
            "in": "header",
            "required": True,
            "description": "A UUID the client makes once for each create request (api-design.md).",
            "schema": {"type": "string", "format": "uuid"},
        }
        declaring = _idempotent_operations(app)
        for path, item in schema["paths"].items():
            if path == HEALTH_PATH:
                continue
            for method, operation in item.items():
                if (method, path) not in declaring:
                    continue
                operation.setdefault("parameters", []).append(
                    {"$ref": f"#/components/parameters/{KEY_PARAMETER}"}
                )
                responses = operation["responses"]
                responses.setdefault("400", _error_response("The request is not valid"))
                _document_409(responses)
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
