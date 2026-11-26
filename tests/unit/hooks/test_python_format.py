"""The PostToolUse hook formats a Python file with ruff, and never touches tests or other files.

A fake `uv` on PATH records how it is called and exits with a chosen code, so no real ruff run
(and no network) is needed.
"""

from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

from tests.unit.hooks.support import run_hook

FAKE_UV = """#!/bin/sh
echo "$@" >> "$FAKE_UV_LOG"
if [ "$1 $2 $3" = "run ruff check" ] && [ -n "$FAKE_RUFF_CHECK_FAILS" ]; then
  echo "app/x.py:1:1: F401 unused import"
  exit 1
fi
if [ "$1 $2 $3" = "run ruff format" ] && [ -n "$FAKE_RUFF_FORMAT_FAILS" ]; then
  echo "error: invalid syntax" >&2
  exit 2
fi
exit 0
"""


def project(tmp_path: Path) -> tuple[Path, dict[str, str], Path]:
    root = tmp_path / "proj"
    (root / "app").mkdir(parents=True)
    (root / "tests" / "unit").mkdir(parents=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(FAKE_UV)
    uv.chmod(uv.stat().st_mode | stat.S_IXUSR)
    log = tmp_path / "uv.log"
    env = {
        "CLAUDE_PROJECT_DIR": str(root),
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "FAKE_UV_LOG": str(log),
    }
    return root, env, log


def edit(path: Path, env: dict[str, str], tool: str = "Edit") -> subprocess.CompletedProcess[str]:
    return run_hook("python_format.py", tool, {"file_path": str(path)}, env=env)


def test_formats_and_fixes_a_python_file(tmp_path: Path) -> None:
    root, env, log = project(tmp_path)
    target = root / "app" / "x.py"
    target.write_text("x=1\n")
    result = edit(target, env)
    assert result.returncode == 0
    assert result.stdout == "" and result.stderr == ""
    assert log.read_text().splitlines() == [
        f"run ruff format {target.resolve()}",
        f"run ruff check --fix {target.resolve()}",
    ]


def test_relative_path_is_resolved_from_the_project_root(tmp_path: Path) -> None:
    root, env, log = project(tmp_path)
    (root / "app" / "x.py").write_text("x=1\n")
    result = run_hook("python_format.py", "Write", {"file_path": "app/x.py"}, env=env)
    assert result.returncode == 0
    assert len(log.read_text().splitlines()) == 2


def test_unfixable_lint_problem_exits_2_with_ruffs_output(tmp_path: Path) -> None:
    root, env, _ = project(tmp_path)
    target = root / "app" / "x.py"
    target.write_text("import os\n")
    result = edit(target, {**env, "FAKE_RUFF_CHECK_FAILS": "1"})
    assert result.returncode == 2
    assert "F401 unused import" in result.stderr
    assert "app/x.py" in result.stderr


def test_format_failure_exits_2(tmp_path: Path) -> None:
    root, env, _ = project(tmp_path)
    target = root / "app" / "x.py"
    target.write_text("def (:\n")
    result = edit(target, {**env, "FAKE_RUFF_FORMAT_FAILS": "1"})
    assert result.returncode == 2
    assert "invalid syntax" in result.stderr


def test_files_under_tests_are_skipped(tmp_path: Path) -> None:
    root, env, log = project(tmp_path)
    target = root / "tests" / "unit" / "test_x.py"
    target.write_text("x=1\n")
    assert edit(target, {**env, "FAKE_RUFF_CHECK_FAILS": "1"}).returncode == 0
    assert not log.exists()
    assert target.read_text() == "x=1\n"


def test_non_python_files_are_skipped(tmp_path: Path) -> None:
    root, env, log = project(tmp_path)
    for name in ("README.md", "pyproject.toml", "app/data.json"):
        target = root / name
        target.write_text("x\n")
        assert edit(target, env).returncode == 0
    assert not log.exists()


def test_missing_file_and_outside_root_are_skipped(tmp_path: Path) -> None:
    root, env, log = project(tmp_path)
    assert edit(root / "app" / "gone.py", env).returncode == 0
    outside = tmp_path / "outside.py"
    outside.write_text("x=1\n")
    assert edit(outside, env).returncode == 0
    assert not log.exists()


def test_missing_uv_does_not_fail_the_edit(tmp_path: Path) -> None:
    root, env, _ = project(tmp_path)
    target = root / "app" / "x.py"
    target.write_text("x=1\n")
    result = edit(target, {**env, "PATH": "/nonexistent"})
    assert result.returncode == 0
