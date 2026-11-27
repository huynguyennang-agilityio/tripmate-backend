"""F4 Error format and error codes: cases F4-1 to F4-5 (epic-11 spec, section 2.3).

A temporary module under app/ (as in F1-7) raises `AppError`, raises something unexpected,
and takes a body with a schema. The responses are checked against the format of
api-design.md, section 4.1. The interface is in tests/INTERFACE.md, section "F4: Errors".
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import pytest

from tests.support import APP_DIR, build_app, client_for

MODULE_PREFIX = "/v1"
REQUEST_ID = "X-Request-Id"
ERROR_KEYS = {"code", "params", "fields", "requestId"}
CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")

INIT_SOURCE = """\
def register(registry):
    registry.add_error_codes("f4probe.expired")
"""

API_SOURCE = """\
from fastapi import APIRouter
from pydantic import BaseModel

from app.platform.errors import AppError

router = APIRouter()


class ItemBody(BaseModel):
    name: str
    amount: int


@router.get("/{segment}/ok")
async def ok() -> dict[str, str]:
    return {{"ok": "{segment}"}}


@router.get("/{segment}/app-error")
async def app_error() -> dict[str, str]:
    raise AppError(
        status=410,
        code="f4probe.expired",
        params={{"expiredAt": "2026-12-01T10:00:00Z"}},
        fields=None,
    )


@router.post("/{segment}/items")
async def create_item(body: ItemBody) -> dict[str, str]:
    return {{"name": body.name}}


@router.get("/{segment}/unexpected")
async def unexpected() -> dict[str, str]:
    raise RuntimeError("{secret}")
"""


def _device_headers() -> dict[str, str]:
    # A valid device ID (F5), so these requests stay valid once F5 requires it.
    return {"X-Device-Id": str(uuid.uuid4())}


def _probe_module(module_folder, hidden_detail: str = "unused") -> str:
    name = module_folder({"__init__.py": INIT_SOURCE})
    segment = name.replace("_", "-")
    (APP_DIR / name / "api.py").write_text(
        API_SOURCE.format(segment=segment, secret=hidden_detail), encoding="utf-8"
    )
    return f"{MODULE_PREFIX}/{segment}"


def _error_of(response: Any) -> dict[str, Any]:
    """The `error` object of an error response, checked against api-design.md, section 4.1."""
    body = response.json()
    assert isinstance(body, dict) and set(body) == {"error"}, f"body: {response.text}"
    error = body["error"]
    assert isinstance(error, dict) and set(error) == ERROR_KEYS, f"error: {error}"
    request_id = error["requestId"]
    assert isinstance(request_id, str) and request_id, f"requestId: {request_id!r}"
    assert response.headers.get(REQUEST_ID) == request_id, (
        f"{REQUEST_ID} {response.headers.get(REQUEST_ID)!r} != requestId {request_id!r}"
    )
    return error


@pytest.mark.asyncio
async def test_F4_1_app_error_returns_its_status_in_the_error_format_with_request_id(
    set_settings, module_folder
) -> None:
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    base = _probe_module(module_folder)

    async with client_for(build_app()) as client:
        response = await client.get(f"{base}/app-error", headers=_device_headers())

    assert response.status_code == 410, f"{response.status_code} {response.text}"
    error = _error_of(response)
    assert error["code"] == "f4probe.expired"
    assert error["params"] == {"expiredAt": "2026-12-01T10:00:00Z"}
    assert error["fields"] is None


@pytest.mark.asyncio
async def test_F4_2_body_breaking_its_schema_in_two_fields_returns_400_with_two_fields(
    set_settings, module_folder
) -> None:
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    base = _probe_module(module_folder)

    async with client_for(build_app()) as client:
        # `name` is missing and `amount` is not an integer.
        response = await client.post(
            f"{base}/items", json={"amount": "not-a-number"}, headers=_device_headers()
        )

    assert response.status_code == 400, f"{response.status_code} {response.text}"
    error = _error_of(response)
    assert error["code"] == "request.invalid"
    fields = error["fields"]
    assert isinstance(fields, list) and len(fields) == 2, f"fields: {fields}"
    for entry in fields:
        assert isinstance(entry, dict) and set(entry) == {"field", "code"}, f"entry: {entry}"
        assert isinstance(entry["code"], str) and CODE_PATTERN.match(entry["code"]), entry
    assert sorted(entry["field"] for entry in fields) == ["amount", "name"]


@pytest.mark.asyncio
async def test_F4_3_body_that_is_not_json_returns_400_request_invalid(
    set_settings, module_folder
) -> None:
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    base = _probe_module(module_folder)

    async with client_for(build_app()) as client:
        response = await client.post(
            f"{base}/items",
            content=b'{"name": "x", "amount": ',
            headers={**_device_headers(), "Content-Type": "application/json"},
        )

    assert response.status_code == 400, f"{response.status_code} {response.text}"
    assert _error_of(response)["code"] == "request.invalid"


@pytest.mark.asyncio
async def test_F4_4_unexpected_error_returns_500_server_error_with_no_detail(
    set_settings, module_folder
) -> None:
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    secret = f"f4-secret-detail-{uuid.uuid4().hex}"
    base = _probe_module(module_folder, hidden_detail=secret)

    async with client_for(build_app(), raise_app_exceptions=False) as client:
        response = await client.get(f"{base}/unexpected", headers=_device_headers())

    assert response.status_code == 500, f"{response.status_code} {response.text}"
    error = _error_of(response)
    assert error["code"] == "server.error"
    assert error["params"] in ({}, None), f"params: {error['params']}"
    assert error["fields"] is None
    for detail in (secret, "RuntimeError", "Traceback", 'File "'):
        assert detail not in response.text, f"the body holds {detail!r}: {response.text}"


@pytest.mark.asyncio
async def test_F4_5_two_requests_get_two_different_request_ids(set_settings, module_folder) -> None:
    set_settings(RATE_LIMIT_PER_MINUTE="0")
    base = _probe_module(module_folder)

    async with client_for(build_app()) as client:
        first = await client.get(f"{base}/ok", headers=_device_headers())
        second = await client.get(f"{base}/ok", headers=_device_headers())
        failed = [await client.get(f"{base}/app-error", headers=_device_headers()) for _ in "ab"]

    assert first.status_code == 200 and second.status_code == 200
    ids = [r.headers.get(REQUEST_ID) for r in (first, second)]
    assert all(ids), f"a successful response has no {REQUEST_ID}: {ids}"
    assert ids[0] != ids[1], f"both requests got {ids[0]!r}"
    error_ids = [_error_of(r)["requestId"] for r in failed]
    assert error_ids[0] != error_ids[1], f"both errors got {error_ids[0]!r}"
