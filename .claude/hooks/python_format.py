#!/usr/bin/env python3
"""PostToolUse hook for Edit, Write, and MultiEdit: format and lint the Python file just written.

Runs `uv run ruff format <file>` and `uv run ruff check --fix <file>` from the project root
(`CLAUDE_PROJECT_DIR`, else the hook's `cwd`). It skips:

- files that are not `.py`;
- files under a `tests/` folder (the test lock compares the test agent's files as text, so
  nothing may rewrite them);
- files outside the project root, and files that no longer exist.

Exit 0 with no output when ruff is happy. Exit 2, with ruff's remaining problems on stderr,
when ruff reports something it could not fix, so Claude sees it. A missing `uv` is not an
error of the edit: exit 0.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def run_ruff(args: list[str], root: Path) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(  # noqa: S603
            ["uv", "run", "ruff", *args],  # noqa: S607
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool_input = payload.get("tool_input") or {}
    file_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not isinstance(file_path, str) or not file_path.endswith(".py"):
        return 0
    root = Path(os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd") or os.getcwd()).resolve()
    path = Path(file_path)
    path = (path if path.is_absolute() else root / path).resolve()
    try:
        relative = path.relative_to(root)
    except ValueError:
        return 0
    if "tests" in relative.parts or not path.is_file():
        return 0

    problems: list[str] = []
    for args in (["format", str(path)], ["check", "--fix", str(path)]):
        result = run_ruff(args, root)
        if result is None:
            return 0
        if result.returncode != 0:
            problems.append((result.stdout + result.stderr).strip())
    if problems:
        print(f"ruff reports problems in {relative}:\n" + "\n".join(problems), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
