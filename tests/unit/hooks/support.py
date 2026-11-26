"""Run a Claude Code hook script the way Claude Code does: JSON on stdin, exit code, stderr."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

HOOKS = Path(__file__).resolve().parents[3] / ".claude" / "hooks"


def run_hook(
    name: str,
    tool_name: str,
    tool_input: dict[str, object],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    payload = {"tool_name": tool_name, "tool_input": tool_input, "cwd": str(cwd or Path.cwd())}
    return subprocess.run(
        [sys.executable, str(HOOKS / name)],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, **(env or {})},
        timeout=60,
        check=False,
    )
