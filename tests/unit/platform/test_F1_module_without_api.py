"""F1 Backend skeleton and health check: case F1-8 (epic-11 spec, section 7.1)."""

from __future__ import annotations

import pytest

from tests.support import build_app, client_for, openapi_paths


@pytest.mark.asyncio
async def test_F1_8_module_folder_without_api_py_is_skipped_and_app_starts(
    set_settings, module_folder
) -> None:
    set_settings()
    paths_without_folder = openapi_paths(build_app())

    module_folder({"__init__.py": "", "service.py": ""})

    app = build_app()
    async with client_for(app):
        pass  # start-up and shut-down run without an error

    assert openapi_paths(app) == paths_without_folder
