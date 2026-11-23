"""The one error type a module raises, and its response format (api-design.md, section 4.1).

Card F4 adds `request.invalid` with fields, `500 server.error`, the request ID,
and the check that every registered code has text in both languages.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


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


async def _app_error_handler(request: Request, error: Exception) -> JSONResponse:
    if not isinstance(error, AppError):  # registered for AppError only
        raise error
    # The request ID arrives with card F4; until then it is null.
    return JSONResponse(status_code=error.status, content=error_body(error, None))


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _app_error_handler)
