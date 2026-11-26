"""The contract pipeline of EPIC-11 spec, section 2.10 (card F12).

- `stub()` declares a route that is only a contract: marked `x-stub: true`, answering
  `501 server.not_implemented`.
- `generate` writes the app's `openapi.json`; `check` runs the example check on it.
- `contract_app` is the app as the contract job tests it (Schemathesis, in process).

Run `python -m app.platform.contract generate <path>` and `... check <path>`.
"""

from __future__ import annotations

import importlib
import inspect
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel, ConfigDict

from app.platform.config import Settings
from app.platform.errors import AppError

NOT_IMPLEMENTED_CODE = "server.not_implemented"
STUB_MARK = "x-stub"

# `info.version` of the generated file. It must not change with every merge (no release
# version, no commit), or each merge would open a pull request in `tripmate-contracts`.
# The real contract version comes from the tags of that repository (card CT2).
CONTRACT_VERSION = "0.0.0"

HTTP_METHODS = frozenset({"get", "put", "post", "delete", "options", "head", "patch", "trace"})

# FastAPI adds this response itself to a route with parameters; card F4 replaces it with
# `400 request.invalid`, so the check does not ask for an example on it.
FRAMEWORK_RESPONSES = frozenset({"422"})


class _ErrorBody(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "code": NOT_IMPLEMENTED_CODE,
                    "params": {},
                    "fields": None,
                    "requestId": None,
                }
            ]
        }
    )

    code: str
    params: dict[str, Any]
    fields: list[dict[str, str]] | None
    requestId: str | None  # noqa: N815 - the wire name of api-design.md, section 4.1


class NotImplementedResponse(BaseModel):
    """The error format of api-design.md, section 4.1, as a stub answers."""

    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "error": {
                        "code": NOT_IMPLEMENTED_CODE,
                        "params": {},
                        "fields": None,
                        "requestId": None,
                    }
                }
            ]
        }
    )

    error: _ErrorBody


def stub(
    router: APIRouter, method: str, path: str, **route_options: Any
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    """Declare `method path` on `router` as a stub: contract only, answers `501`.

    The decorated function's signature declares the request; its body never runs.
    `route_options` are those of `APIRouter.add_api_route`. Give the response model an
    example (`json_schema_extra={"examples": [...]}`): `check` fails without one.
    """

    def decorator(function: Callable[..., Any]) -> Callable[..., Any]:
        async def not_implemented(*args: Any, **kwargs: Any) -> None:
            raise AppError(501, NOT_IMPLEMENTED_CODE)

        # Same request as the declared function; annotations resolved in its own module.
        not_implemented.__signature__ = inspect.signature(function, eval_str=True)  # type: ignore[attr-defined]
        not_implemented.__name__ = function.__name__
        not_implemented.__qualname__ = function.__qualname__
        not_implemented.__doc__ = function.__doc__

        options = dict(route_options)
        options["openapi_extra"] = {**(options.get("openapi_extra") or {}), STUB_MARK: True}
        options["responses"] = {
            501: {"model": NotImplementedResponse, "description": "Not implemented yet"},
            **(options.get("responses") or {}),
        }
        router.add_api_route(path, not_implemented, methods=[method.upper()], **options)
        return function

    return decorator


def _resolve(contract: dict[str, Any], schema: Any) -> Any:
    """Follow `$ref`s of the form `#/components/...` (a loop stops at the first repeat)."""
    seen: set[str] = set()
    while isinstance(schema, dict) and isinstance(schema.get("$ref"), str):
        ref = schema["$ref"]
        if ref in seen or not ref.startswith("#/"):
            return {}
        seen.add(ref)
        node: Any = contract
        for part in ref[2:].split("/"):
            node = node.get(part, {}) if isinstance(node, dict) else {}
        schema = {**node, **{k: v for k, v in schema.items() if k != "$ref"}}
    return schema


def _has_example(contract: dict[str, Any], schema: Any, depth: int = 0) -> bool:
    schema = _resolve(contract, schema)
    if not isinstance(schema, dict) or depth > 8:
        return False
    if schema.get("examples") or "example" in schema:
        return True
    # A list's example may sit on the schema of its items.
    if schema.get("type") == "array":
        return _has_example(contract, schema.get("items"), depth + 1)
    return False


def check_examples(contract: dict[str, Any]) -> list[str]:
    """One message per stub operation with a response schema that has no example.

    A message names the operation as `<METHOD> <path>`. Operations that are not
    stubs are not checked. The argument is not changed.
    """
    problems: list[str] = []
    for path, item in sorted(contract.get("paths", {}).items()):
        for method, operation in sorted(item.items()):
            if method not in HTTP_METHODS or not isinstance(operation, dict):
                continue
            if operation.get(STUB_MARK) is not True:
                continue
            missing = []
            for status, response in sorted(operation.get("responses", {}).items()):
                if str(status) in FRAMEWORK_RESPONSES or not isinstance(response, dict):
                    continue
                for media_type, media in sorted(response.get("content", {}).items()):
                    if "example" in media or media.get("examples"):
                        continue
                    if not _has_example(contract, media.get("schema")):
                        missing.append(f"{status} {media_type}")
            if missing:
                problems.append(
                    f"{method.upper()} {path}: no example on the response schema of "
                    + ", ".join(missing)
                )
    return problems


def contract_app() -> FastAPI:
    """The app as the contract sees it: every module found by scanning `app/`.

    The settings are fixed placeholders, not the environment: the contract must be the
    same file on every machine, and no service is needed, because the lifespan (which
    would make the engine and the Redis client) is not run to read the contract.
    """
    settings = Settings(
        database_url="postgresql+asyncpg://contract@127.0.0.1/contract",
        redis_url="redis://127.0.0.1/0",
        release_version=CONTRACT_VERSION,
    )
    # `platform` imports no module and no process (import-linter), so the process module
    # is loaded by name: the contract must be the app the API process serves.
    create_app: Callable[[Settings], FastAPI] = importlib.import_module("app.main").create_app
    return create_app(settings)


def render_contract(app: FastAPI | None = None) -> str:
    """The contract as JSON text: sorted keys, indent 2, a newline at the end."""
    document = (app or contract_app()).openapi()
    return json.dumps(document, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def generate(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(render_contract().encode("utf-8"))


def check(path: Path) -> int:
    problems = check_examples(json.loads(path.read_text(encoding="utf-8")))
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in {"generate", "check"}:
        print("usage: python -m app.platform.contract (generate|check) <path>", file=sys.stderr)
        return 2
    path = Path(args[1])
    if args[0] == "generate":
        generate(path)
        return 0
    return check(path)


if __name__ == "__main__":
    raise SystemExit(main())
