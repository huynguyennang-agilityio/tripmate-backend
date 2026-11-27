"""The one error type a module raises, and its response format (api-design.md, section 4.1).

- `AppError` is what a module raises: a status, a code (`<area>.<reason>`), `params`, `fields`.
- `install_error_handlers` turns it, and every request that breaks its schema (`400
  request.invalid`, one `fields` entry per problem), into the body of section 4.1.
- `install_request_id` puts a new request ID on every response (header `X-Request-Id`, equal
  to `requestId` in an error body), answers an `AppError` raised by a middleware, and turns
  anything unexpected into `500 server.error` with that ID and no detail. The full error goes to the process log only; sending it to error
  reporting is card F10.
- `install_openapi` documents `400` in the 4.1 format (`ErrorResponse`) in place of FastAPI's
  `422 HTTPValidationError`.
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
from pydantic import BaseModel, ConfigDict
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

REQUEST_ID_HEADER = "X-Request-Id"

REQUEST_INVALID = "request.invalid"
SERVER_ERROR = "server.error"
SERVER_UNAVAILABLE = "server.unavailable"
NOT_IMPLEMENTED = "server.not_implemented"
FIELD_MISSING = "request.field_missing"
FIELD_INVALID = "request.field_invalid"
NOT_FOUND = "request.not_found"
METHOD_NOT_ALLOWED = "request.method_not_allowed"
NOT_SIGNED_IN = "auth.not_signed_in"
TOKEN_EXPIRED = "auth.token_expired"  # noqa: S105 (an error code)
RATE_LIMITED = "rate.limited"

# Codes the platform returns itself; each needs text in `tripmate-contracts`.
# (`server.unavailable` is returned by the health check and the rate limit, cards F1 and F6;
# `server.not_implemented` by a stub route, card F12; the two `auth.` codes by the access check,
# card F5; `rate.limited` by the rate limits, card F6.)
# The two field codes are all a `fields` entry can carry, so the set stays closed.
PLATFORM_ERROR_CODES: frozenset[str] = frozenset(
    {
        REQUEST_INVALID,
        SERVER_ERROR,
        SERVER_UNAVAILABLE,
        NOT_IMPLEMENTED,
        FIELD_MISSING,
        FIELD_INVALID,
        NOT_FOUND,
        METHOD_NOT_ALLOWED,
        NOT_SIGNED_IN,
        TOKEN_EXPIRED,
        RATE_LIMITED,
    }
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
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(code)
        self.status = status
        self.code = code
        self.params = params or {}
        self.fields = fields
        self.headers = headers  # extra response headers, such as `Retry-After`


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
    return JSONResponse(status_code=error.status, content=content, headers=error.headers)


async def _validation_error_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, RequestValidationError):  # registered for it only
        raise error
    fields = [_field_problem(dict(problem)) for problem in error.errors()]
    invalid = AppError(400, REQUEST_INVALID, fields=fields)
    return JSONResponse(status_code=400, content=error_body(invalid, _request_id_of(request)))


async def _http_exception_handler(request: Request, error: Exception) -> JSONResponse:
    """Only an unknown route (404) and a wrong method (405) are expected `HTTPException`s.

    They keep their status and headers (`Allow`), without the detail. Any other `HTTPException`
    is unexpected: modules raise `AppError` for their own errors. It is answered `500
    server.error` with the request ID and no detail, and its status is logged.
    """
    if not isinstance(error, StarletteHTTPException):  # registered for it only
        raise error
    request_id = _request_id_of(request)
    if error.status_code in (404, 405):
        code = NOT_FOUND if error.status_code == 404 else METHOD_NOT_ALLOWED
        content = error_body(AppError(error.status_code, code), request_id)
        return JSONResponse(status_code=error.status_code, content=content, headers=error.headers)
    logger.error(
        "Unexpected HTTPException with status %s in request %s: %s %s",
        error.status_code,
        request_id,
        request.method,
        request.url.path,
    )
    return JSONResponse(
        status_code=500, content=error_body(AppError(500, SERVER_ERROR), request_id)
    )


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
    app.add_exception_handler(RequestValidationError, _validation_error_handler)
    app.add_exception_handler(StarletteHTTPException, _http_exception_handler)


class FieldProblem(BaseModel):
    field: str
    code: str


class ErrorDetail(BaseModel):
    code: str
    params: dict[str, Any]
    fields: list[FieldProblem] | None
    requestId: str  # noqa: N815 (the name in the contract)


class ErrorResponse(BaseModel):
    """The body of every error response (api-design.md, section 4.1)."""

    # An example, so a stub whose request has parameters (it documents `400`) passes the
    # example check of card F12.
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": REQUEST_INVALID,
                        "params": {},
                        "fields": [{"field": "name", "code": FIELD_MISSING}],
                        "requestId": "01JD0000000000000000000000",
                    }
                }
            ]
        }
    )

    error: ErrorDetail


def install_openapi(app: FastAPI) -> None:
    """Document `400 ErrorResponse` where FastAPI documents `422 HTTPValidationError`."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            schema = FastAPI.openapi(app)
            components = schema.setdefault("components", {}).setdefault("schemas", {})
            for name in ("HTTPValidationError", "ValidationError"):
                components.pop(name, None)
            model = ErrorResponse.model_json_schema(ref_template="#/components/schemas/{model}")
            components.update(model.pop("$defs", {}))
            components["ErrorResponse"] = model
            for item in schema.get("paths", {}).values():
                for operation in item.values():
                    responses = operation.get("responses", {})
                    if responses.pop("422", None) is not None:
                        responses["400"] = {
                            "description": "The request is not valid (api-design.md, 4.1)",
                            "content": {
                                "application/json": {
                                    "schema": {"$ref": "#/components/schemas/ErrorResponse"}
                                }
                            },
                        }
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]


class RequestIdMiddleware:
    """Gives each HTTP request a new ID and answers anything unexpected with `500 server.error`.

    Pure ASGI, so it sees every response, including the 500 it builds itself (a handler
    registered for `Exception` would run outside the middleware and miss the header). The
    unexpected error is logged here with its stack; the response holds neither.

    It must be the outermost middleware: `create_app` installs it last. Inside it, an `AppError`
    that another middleware raises (`ExceptionMiddleware` sits inside every user middleware, so
    it never sees one) is answered here with its own status and body.
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
        except Exception as error:
            if started:  # the response is on its way; nothing left to answer
                raise
            if isinstance(error, AppError):
                status, body = error.status, error_body(error, request_id)
            else:
                logger.exception("Unexpected error in request %s", request_id)
                status, body = 500, error_body(AppError(500, SERVER_ERROR), request_id)
            headers = error.headers if isinstance(error, AppError) else None
            await JSONResponse(status_code=status, content=body, headers=headers)(
                scope, receive, send_with_id
            )


def install_request_id(app: FastAPI) -> None:
    app.add_middleware(RequestIdMiddleware)
