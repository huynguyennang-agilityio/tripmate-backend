"""F17 Examples on every success response: cases F17-1 to F17-4 (level C; EPIC-11 spec, 2.10).

The interface is in tests/INTERFACE.md, section "F17: Examples on every success response".
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tests.contract.platform.f12_support import MODULE_PREFIX, generate, run_contract
from tests.support import APP_DIR

ROUTES_SOURCE = """\
from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict

router = APIRouter()


class WithExample(BaseModel):
    model_config = ConfigDict(json_schema_extra={{"examples": [{{"name": "Da Lat"}}]}})

    name: str


class WithoutExample(BaseModel):
    title: str

{routes}"""

BARE_ROUTE = """

@router.get("/{segment}", response_model=WithoutExample)
async def bare() -> WithoutExample:
    return WithoutExample(title="x")
"""

GOOD_ROUTE = """

@router.get("/{segment}", response_model=WithExample)
async def good() -> WithExample:
    return WithExample(name="Da Lat")
"""

NO_CONTENT_ROUTE = """

@router.delete("/{segment}", status_code=204, response_class=Response)
async def gone() -> Response:
    return Response(status_code=204)
"""

ERROR_WITHOUT_EXAMPLE_ROUTE = """

@router.get(
    "/{segment}",
    response_model=WithExample,
    responses={{404: {{"model": WithoutExample, "description": "Not there"}}}},
)
async def maybe() -> WithExample:
    return WithExample(name="Da Lat")
"""


def _write_routes(module_folder, routes: str) -> str:
    name = module_folder({"__init__.py": ""})
    (APP_DIR / name / "api.py").write_text(ROUTES_SOURCE.format(routes=routes), encoding="utf-8")
    return name


def _resolve(contract: dict[str, Any], schema: Any) -> Any:
    while isinstance(schema, dict) and "$ref" in schema:
        node: Any = contract
        for part in schema["$ref"][2:].split("/"):
            node = node[part]
        schema = node
    return schema


def test_F17_1_route_without_example_on_200_fails_the_check_naming_it(
    tmp_path: Path, module_folder
) -> None:
    from app.platform.contract import check_examples

    segment = f"bare-{module_folder({'__init__.py': ''}).replace('_', '-')}"
    _write_routes(module_folder, BARE_ROUTE.format(segment=segment))
    failing = tmp_path / "openapi.json"
    contract = generate(failing)

    assert contract["paths"][f"{MODULE_PREFIX}/{segment}"]["get"].get("x-stub") is not True
    problems = check_examples(contract)
    operation = f"GET {MODULE_PREFIX}/{segment}"
    assert any(operation in problem for problem in problems), problems

    result = run_contract("check", str(failing))
    assert result.returncode != 0, "check passed a route with no example on its 200 response"
    assert operation in result.stdout + result.stderr


def test_F17_2_the_apps_contract_passes_and_health_has_an_example(tmp_path: Path) -> None:
    from app.platform.contract import check_examples

    path = tmp_path / "openapi.json"
    contract = generate(path)

    assert check_examples(contract) == []
    result = run_contract("check", str(path))
    assert result.returncode == 0, (
        f"check exited {result.returncode}: {result.stdout}{result.stderr}"
    )

    media = contract["paths"][f"{MODULE_PREFIX}/health"]["get"]["responses"]["200"]["content"][
        "application/json"
    ]
    if "example" in media:
        example = media["example"]
    else:
        schema = _resolve(contract, media["schema"])
        examples = schema.get("examples") or ([schema["example"]] if "example" in schema else [])
        assert examples, "GET /v1/health 200 has no example"
        example = examples[0]
    assert isinstance(example, dict)
    assert {"status", "version"} <= set(example), example


def test_F17_3_no_content_response_and_a_200_with_example_are_not_named(
    tmp_path: Path, module_folder
) -> None:
    from app.platform.contract import check_examples

    base = module_folder({"__init__.py": ""}).replace("_", "-")
    gone, good, bare = f"gone-{base}", f"good-{base}", f"bare-{base}"
    _write_routes(
        module_folder,
        NO_CONTENT_ROUTE.format(segment=gone)
        + GOOD_ROUTE.format(segment=good)
        + BARE_ROUTE.format(segment=bare),
    )
    problems = check_examples(generate(tmp_path / "openapi.json"))

    # Control: a route of the same module with no example is named.
    assert any(f"GET {MODULE_PREFIX}/{bare}" in problem for problem in problems), problems

    assert not any(f"DELETE {MODULE_PREFIX}/{gone}" in problem for problem in problems), problems
    assert not any(f"GET {MODULE_PREFIX}/{good}" in problem for problem in problems), problems


def test_F17_4_an_error_response_without_example_on_a_route_that_is_not_a_stub_is_not_named(
    tmp_path: Path, module_folder
) -> None:
    from app.platform.contract import check_examples

    base = module_folder({"__init__.py": ""}).replace("_", "-")
    segment, bare = f"maybe-{base}", f"bare-{base}"
    _write_routes(
        module_folder,
        ERROR_WITHOUT_EXAMPLE_ROUTE.format(segment=segment) + BARE_ROUTE.format(segment=bare),
    )
    contract = generate(tmp_path / "openapi.json")

    assert "404" in contract["paths"][f"{MODULE_PREFIX}/{segment}"]["get"]["responses"]
    problems = check_examples(contract)
    # Control: a route of the same module with no example on its 200 is named.
    assert any(f"GET {MODULE_PREFIX}/{bare}" in problem for problem in problems), problems
    assert not any(f"GET {MODULE_PREFIX}/{segment}" in problem for problem in problems), problems
