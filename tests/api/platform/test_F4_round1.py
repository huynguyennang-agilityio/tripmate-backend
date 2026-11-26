"""F4 round 1: tests of the code agent (not locked tests of the test agent).

An `AppError` raised by a middleware, errors from `HTTPException`, and the OpenAPI document.
"""

from __future__ import annotations

import re

import pytest
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.platform.errors import AppError
from tests.support import APP_DIR, build_app, client_for

ERROR_KEYS = {"code", "params", "fields", "requestId"}

API_SOURCE = """\
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter()


class Body(BaseModel):
    name: str


@router.get("/{segment}/ok")
async def ok() -> dict[str, str]:
    return {{"ok": "yes"}}


@router.get("/{segment}/teapot")
async def teapot() -> dict[str, str]:
    raise HTTPException(status_code=418, detail="secret detail")


@router.get("/{segment}/broken")
async def broken() -> dict[str, str]:
    raise HTTPException(status_code=503, detail="secret detail")


@router.post("/{segment}/items")
async def create(body: Body) -> dict[str, str]:
    return {{"name": body.name}}
"""


class RaisingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):  # type: ignore[no-untyped-def]
        raise AppError(429, "rate.limited", params={"retryAfter": 3})


def _probe(module_folder) -> str:
    name = module_folder({"__init__.py": ""})
    segment = name.replace("_", "-")
    (APP_DIR / name / "api.py").write_text(API_SOURCE.format(segment=segment), encoding="utf-8")
    return f"/v1/{segment}"


def _error_of(response) -> dict:
    error = response.json()["error"]
    assert set(error) == ERROR_KEYS, error
    assert error["requestId"] == response.headers["X-Request-Id"]
    return error


@pytest.mark.asyncio
async def test_F4_r1_app_error_from_a_middleware_keeps_status_and_body(
    set_settings, module_folder
) -> None:
    set_settings()
    base = _probe(module_folder)
    app = build_app()
    app.user_middleware.append(Middleware(RaisingMiddleware))  # the innermost user middleware

    async with client_for(app, raise_app_exceptions=False) as client:
        response = await client.get(f"{base}/ok")

    assert response.status_code == 429, response.text
    error = _error_of(response)
    assert error["code"] == "rate.limited"
    assert error["params"] == {"retryAfter": 3}


@pytest.mark.asyncio
async def test_F4_r1_unknown_route_and_wrong_method_use_the_error_format(
    set_settings, module_folder
) -> None:
    set_settings()
    base = _probe(module_folder)

    async with client_for(build_app()) as client:
        missing = await client.get("/v1/no-such-route")
        wrong_method = await client.put(f"{base}/ok")

    assert missing.status_code == 404
    assert _error_of(missing)["code"] == "request.not_found"
    assert wrong_method.status_code == 405
    assert _error_of(wrong_method)["code"] == "request.method_not_allowed"
    assert "GET" in wrong_method.headers.get("allow", "")


@pytest.mark.asyncio
async def test_F4_r1_http_exception_keeps_status_and_drops_detail(
    set_settings, module_folder
) -> None:
    set_settings()
    base = _probe(module_folder)

    async with client_for(build_app()) as client:
        client_error = await client.get(f"{base}/teapot")
        server_error = await client.get(f"{base}/broken")

    assert client_error.status_code == 418
    assert _error_of(client_error)["code"] == "request.invalid"
    assert server_error.status_code == 503
    assert _error_of(server_error)["code"] == "server.error"
    assert "secret detail" not in client_error.text + server_error.text


def test_F4_r1_openapi_documents_400_error_response_not_422(set_settings, module_folder) -> None:
    set_settings()
    _probe(module_folder)
    schema = build_app().openapi()

    statuses = {
        status
        for item in schema["paths"].values()
        for operation in item.values()
        for status in operation["responses"]
    }
    assert "422" not in statuses and "400" in statuses, statuses
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "ErrorResponse" in schema["components"]["schemas"]
    dumped = str(schema)
    assert not re.search(r"HTTPValidationError|ValidationError'", dumped), dumped
