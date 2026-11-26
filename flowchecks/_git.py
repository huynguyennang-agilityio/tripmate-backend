"""Git helpers shared by the checks: commits under check, trailers, and file contents."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import PurePosixPath

TEST_TRAILER_KEY = "Agent"
TEST_TRAILER_VALUE = "test"


class GitError(RuntimeError):
    """A git command failed. The check cannot give an answer, so it must not pass."""


@dataclass(frozen=True)
class Commit:
    sha: str
    is_test: bool


def git(repo: str | os.PathLike[str], *args: str) -> bytes:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        check=False,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip()
        raise GitError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def git_text(repo: str | os.PathLike[str], *args: str) -> str:
    return git(repo, *args).decode("utf-8", "replace").strip()


def merge_base(repo: str | os.PathLike[str], base: str, head: str) -> str:
    """Where the branch left `base`: the tree the pull request is compared with.

    When `base` has moved on since the branch was cut, comparing against `base` itself would
    show the work of other pull requests as this one's.
    """
    try:
        return git_text(repo, "merge-base", base, head)
    except GitError:
        return git_text(repo, "rev-parse", "--verify", f"{base}^{{commit}}")


def commits(repo: str | os.PathLike[str], base: str, head: str) -> list[Commit]:
    """The commits of `base..head`, oldest first, merge commits left out.

    A test commit is one whose message has the trailer `Agent: test`.
    """
    unit, record = "\x1f", "\x1e"
    fmt = f"%H{unit}%(trailers:key={TEST_TRAILER_KEY},valueonly,unfold,separator=%x2c){record}"
    out = git(repo, "log", "--reverse", "--no-merges", f"--format={fmt}", f"{base}..{head}")
    found: list[Commit] = []
    for entry in out.decode("utf-8", "replace").split(record):
        entry = entry.strip()
        if not entry:
            continue
        sha, _, values = entry.partition(unit)
        is_test = TEST_TRAILER_VALUE in {v.strip().lower() for v in values.split(",")}
        found.append(Commit(sha=sha.strip(), is_test=is_test))
    return found


def show_file(repo: str | os.PathLike[str], rev: str, path: str) -> str | None:
    """The text of `path` at `rev`, or None when the file does not exist there."""
    try:
        return git(repo, "show", f"{rev}:{path}").decode("utf-8", "replace")
    except GitError:
        return None


def changed_files(repo: str | os.PathLike[str], old: str, new: str) -> list[str]:
    """Paths that differ between two revisions (renames shown as a delete and an add)."""
    out = git(repo, "diff", "--name-only", "--no-renames", "-z", old, new)
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def is_test_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return name.startswith("test_") and name.endswith(".py")


TEST_FOLDERS = ("tests", "test")


def in_test_folder(path: str) -> bool:
    return (
        PurePosixPath(path).parts[:1] in [(f,) for f in TEST_FOLDERS]
        and len(PurePosixPath(path).parts) > 1
    )


def is_support_file(path: str) -> bool:
    """A file under `tests/` that is not a test file: `conftest.py`, helpers, data, interface."""
    return in_test_folder(path) and not is_test_file(path)
