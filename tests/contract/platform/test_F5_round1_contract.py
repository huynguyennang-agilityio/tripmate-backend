"""F5 round 1 (code agent's own tests): the generated `openapi.json` of the real app.

The header `X-Device-Id` and the `400` on every operation but the health check, the bearer
scheme, and `security` plus `401` on the `signed_in` route `GET /v1/platform/whoami`.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from fastapi.routing import iter_route_contexts

from app.main import create_app
from app.platform.access import undeclared_routes
from app.platform.config import Settings
from tests.contract.platform.f12_support import CONTRACT_MODULE
from tests.support import REPO_ROOT, process_env


def _settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://u@127.0.0.1/d",
        redis_url="redis://127.0.0.1/0",
        release_version="x",
    )


def _generate(tmp_path: Path) -> dict[str, object]:
    target = tmp_path / "openapi.json"
    result = subprocess.run(
        [sys.executable, "-m", CONTRACT_MODULE, "generate", str(target)],
        cwd=REPO_ROOT,
        env=process_env({}),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    loaded: dict[str, object] = json.loads(target.read_text(encoding="utf-8"))
    return loaded


def test_F5_R1_generated_contract_declares_the_device_id_the_bearer_scheme_and_the_errors(
    tmp_path: Path,
) -> None:
    contract = _generate(tmp_path)
    device_ref = {"$ref": "#/components/parameters/DeviceId"}
    error_ref = {"$ref": "#/components/schemas/ErrorResponse"}
    components = contract["components"]
    assert isinstance(components, dict)
    parameter = components["parameters"]["DeviceId"]
    assert (parameter["name"], parameter["in"], parameter["required"]) == (
        "X-Device-Id",
        "header",
        True,
    )
    assert parameter["schema"] == {"type": "string", "format": "uuid"}
    assert components["securitySchemes"]["bearerAuth"] == {
        "type": "http",
        "scheme": "bearer",
        "bearerFormat": "JWT",
    }
    paths = contract["paths"]
    assert isinstance(paths, dict)
    for path, item in paths.items():
        for method, operation in item.items():
            if path == "/v1/health":
                assert device_ref not in operation.get("parameters", [])
                assert "400" not in operation["responses"]
                continue
            assert device_ref in operation["parameters"], (method, path)
            body = operation["responses"]["400"]["content"]["application/json"]["schema"]
            assert body == error_ref, (method, path)
    assert "security" not in paths["/v1/health"]["get"]


def test_F5_R1_6_whoami_is_not_in_the_contract_but_still_declares_its_access(
    tmp_path: Path,
) -> None:
    contract = _generate(tmp_path)
    assert "/v1/platform/whoami" not in contract["paths"]  # type: ignore[operator]
    assert "whoami" not in json.dumps(contract).lower()
    app = create_app(_settings())
    served = {c.path for c in iter_route_contexts(app.routes)}
    assert "/v1/platform/whoami" in served
    # It is an APIRoute with `signed_in`: the declaration check sees it and finds it declared.
    assert not any("whoami" in route for route in undeclared_routes(app))
    assert not undeclared_routes(app)
