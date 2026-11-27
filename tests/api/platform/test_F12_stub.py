"""F12 Contract publishing: case F12-2, level A (epic-11 spec, section 2.10)."""

from __future__ import annotations

import uuid

import pytest

from tests.contract.platform.f12_support import (
    ERROR_KEYS,
    MODULE_PREFIX,
    NOT_IMPLEMENTED,
    stub_api_source,
)
from tests.support import APP_DIR, build_app, client_for

# Epic-11 spec section 2.2, row "Device ID": every request except the health check carries one.
DEVICE = {"X-Device-Id": str(uuid.uuid4())}


@pytest.mark.asyncio
async def test_F12_2_calling_a_stub_route_returns_501_not_implemented(
    set_settings, module_folder
) -> None:
    set_settings()
    name = module_folder({"__init__.py": ""})
    segment = name.replace("_", "-")
    (APP_DIR / name / "api.py").write_text(stub_api_source(segment), encoding="utf-8")

    path = f"{MODULE_PREFIX}/{segment}"
    async with client_for(build_app()) as client:
        response = await client.get(path, headers=DEVICE)

    assert response.status_code == 501, f"GET {path}: {response.status_code} {response.text}"
    body = response.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == ERROR_KEYS, body
    assert body["error"]["code"] == NOT_IMPLEMENTED
