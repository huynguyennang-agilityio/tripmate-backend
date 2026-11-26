"""Throwaway git repositories for the F14 flow-check tests (tests/INTERFACE.md, section "F14").

Every repository lives under the test's `tmp_path`, has its own local user, and never
touches the real repository's history.
"""

from __future__ import annotations

import importlib
import os
import shlex
import subprocess
import sys
from pathlib import Path
from types import ModuleType

from tests.support import REPO_ROOT

TEST_TRAILER = "Agent: test"

# The test command the failing-first check is given: the project's own pytest, with no
# cache folder written into the repository under check.
TEST_COMMAND = (sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider")

BASE_FILES = {
    "README.md": "# Probe repository\n",
    ".gitignore": "__pycache__/\n.pytest_cache/\n.hypothesis/\n",
    "pytest.ini": "[pytest]\npythonpath = .\n",
    "calc.py": "def add(a, b):\n    raise NotImplementedError\n",
    "tests/test_old.py": "def test_old_passes():\n    assert 1 + 1 == 2\n",
}


def _git_env() -> dict[str, str]:
    env = dict(os.environ)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        env=_git_env(),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr}"
    return result.stdout.strip()


def init_repo(path: Path, files: dict[str, str] | None = None) -> tuple[Path, str]:
    """A new repository at `path` with one base commit. Returns (repo, base sha)."""
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q", "-b", "main")
    git(path, "config", "user.name", "F14 Test")
    git(path, "config", "user.email", "f14-test@example.com")
    git(path, "config", "commit.gpgsign", "false")
    git(path, "config", "core.hooksPath", "/dev/null")
    base = commit(path, BASE_FILES if files is None else files, "Base")
    return path, base


def commit(repo: Path, files: dict[str, str | None], message: str, *, test: bool = False) -> str:
    """Write (or, for None, delete) `files`, commit them, and return the commit's sha.

    With `test=True` the message ends with the trailer `Agent: test`.
    """
    for name, content in files.items():
        target = repo / name
        if content is None:
            git(repo, "rm", "-q", "-r", "--", name)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        git(repo, "add", "--", name)
    full = f"{message}\n\n{TEST_TRAILER}\n" if test else f"{message}\n"
    git(repo, "commit", "-q", "--allow-empty", "-m", full)
    return git(repo, "rev-parse", "HEAD")


def flowcheck(name: str) -> ModuleType:
    """Import `flowchecks.<name>` from the repository root."""
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    return importlib.import_module(f"flowchecks.{name}")


def run_cli(name: str, *args: str, timeout: float = 300) -> subprocess.CompletedProcess[str]:
    """Run `python -m flowchecks.<name> <args>` from the repository root."""
    return subprocess.run(
        [sys.executable, "-m", f"flowchecks.{name}", *args],
        cwd=REPO_ROOT,
        env=_git_env(),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def output(result: subprocess.CompletedProcess[str]) -> str:
    return f"{result.stdout}\n{result.stderr}"


def cli_test_command() -> str:
    return shlex.join(TEST_COMMAND)
