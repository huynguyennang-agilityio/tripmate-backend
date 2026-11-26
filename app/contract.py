"""The contract process (EPIC-11 spec, section 2.10; card F12).

`python -m app.contract generate <path>` writes the app's `openapi.json`;
`python -m app.contract check <path>` runs the example check on it (`check_examples` of
`app/platform/contract.py`). Neither needs a setting or a service.

`served_app` is the app built from the environment as the API process builds it; the CI job
`contract` points Schemathesis at it (`--app app.contract:served_app`), with the lifespan.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.main import create_app
from app.platform.config import Settings
from app.platform.contract import CONTRACT_VERSION, check_examples


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


def __getattr__(name: str) -> Any:
    # Built on first use, so importing this module needs no setting.
    if name == "served_app":
        return create_app()
    raise AttributeError(name)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 2 or args[0] not in {"generate", "check"}:
        print("usage: python -m app.contract (generate|check) <path>", file=sys.stderr)
        return 2
    path = Path(args[1])
    if args[0] == "generate":
        generate(path)
        return 0
    return check(path)


if __name__ == "__main__":
    raise SystemExit(main())
