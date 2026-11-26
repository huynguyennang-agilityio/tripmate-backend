"""The one error type a module raises, and its response format (api-design.md, section 4.1).

- `AppError` is what a module raises: a status, a code (`<area>.<reason>`), `params`, `fields`.
- `install_error_handlers` turns it, and every request that breaks its schema (`400
  request.invalid`, one `fields` entry per problem), into the body of section 4.1.
- `install_request_id` puts a new request ID on every response (header `X-Request-Id`, equal
  to `requestId` in an error body) and turns anything unexpected into `500 server.error` with
  that ID and no detail. The full error goes to the process log only; sending it to error
  reporting is card F10.
- `PLATFORM_ERROR_CODES` are the codes the platform itself returns. With the codes the modules
  register (`Registry.add_error_codes`), they are what `flowchecks.error_codes` checks for text
  in both languages.
"""

from __future__ import annotations

import logging
import secrets
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-Id"

REQUEST_INVALID = "request.invalid"
SERVER_ERROR = "server.error"
SERVER_UNAVAILABLE = "server.unavailable"
FIELD_MISSING = "request.field_missing"
FIELD_INVALID = "request.field_invalid"

# Codes the platform returns itself; each needs text in `tripmate-contracts`.
# (`server.unavailable` is returned by the health check and the rate limit, cards F1 and F6.)
# The two field codes are all a `fields` entry can carry, so the set stays closed.
PLATFORM_ERROR_CODES: frozenset[str] = frozenset(
    {REQUEST_INVALID, SERVER_ERROR, SERVER_UNAVAILABLE, FIELD_MISSING, FIELD_INVALID}
)

_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


class AppError(Exception):
    """An error with a status, a code (`<area>.<reason>`), `params`, and `fields`."""

    def __init__(
        self,
        status: int,
        code: str,
        params: dict[str, Any] | None = None,
        fields: list[dict[str, str]] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.params = params or {}
        self.fields = fields


def error_body(error: AppError, request_id: str | None) -> dict[str, Any]:
    return {
        "error": {
            "code": error.code,
            "params": error.params,
            "fields": error.fields,
            "requestId": request_id,
        }
    }


def new_request_id() -> str:
    """A ULID-like ID: 48 bits of milliseconds then 80 random bits, 26 Crockford base32 characters.

    Sortable by time and unguessable; a new one for every request.
    """
    value = (int(time.time() * 1000) << 80) | secrets.randbits(80)
    return "".join(_CROCKFORD[(value >> shift) & 31] for shift in range(125, -1, -5))


def _request_id_of(request: Request) -> str | None:
    request_id = request.scope.get("state", {}).get("request_id")
    return request_id if isinstance(request_id, str) else None


def _field_problem(problem: dict[str, Any]) -> dict[str, str]:
    """One `{field, code}` entry from one Pydantic error."""
    kind = str(problem.get("type", "invalid"))
    # `loc` starts with where the value came from (body, query, path, header, cookie); a body
    # field is named as in the schema, so the origin is dropped.
    path = [str(part) for part in tuple(problem.get("loc", ()))[1:]]
    if kind == "json_invalid":  # `loc` is the position in the text, not a field
        path = []
    field = ".".join(path) or "body"
    return {"field": field, "code": FIELD_MISSING if kind == "missing" else FIELD_INVALID}


async def _app_error_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, AppError):  # registered for AppError only
        raise error
    content = error_body(error, _request_id_of(request))
    return JSONResponse(status_code=error.status, content=content)


async def _validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, RequestValidationError):  # registered for it only
        raise error
    fields = [_field_problem(dict(problem)) for problem in error.errors()]
    invalid = AppError(400, REQUEST_INVALID, fields=fields)
    return JSONResponse(status_code=400, content=error_body(invalid, _request_id_of(request)))


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)


class RequestIdMiddleware:
    """Gives each HTTP request a new ID and answers anything unexpected with `500 server.error`.

    Pure ASGI, so it sees every response, including the 500 it builds itself (a handler
    registered for `Exception` would run outside the middleware and miss the header). The
    unexpected error is logged here with its stack; the response holds neither.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = new_request_id()
        scope.setdefault("state", {})["request_id"] = request_id
        started = False

        async def send_with_id(message: Message) -> None:
            nonlocal started
            if message["type"] == "http.response.start":
                started = True
                headers = [(k, v) for k, v in message.get("headers", []) if k != b"x-request-id"]
                headers.append((REQUEST_ID_HEADER.lower().encode(), request_id.encode()))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        except Exception:
            if started:  # the response is on its way; nothing left to answer
                raise
            logger.exception("Unexpected error in request %s", request_id)
            body = error_body(AppError(500, SERVER_ERROR), request_id)
            await JSONResponse(status_code=500, content=body)(scope, receive, send_with_id)


def install_request_id(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)
