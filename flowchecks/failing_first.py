"""Failing first: a new test of a test commit must fail before the code exists.

Each test commit of `base..head` is checked out, in order, and the tests that commit adds (IDs
its parent's tree lacks) are run there; each must fail (pytest exit 1) or error (exit 2, or exit 4
when the output has `ERROR collecting <the test's own file>`, which is how pytest answers a test ID
whose module fails to import). A test commit that only changes existing tests, such as a correction
after the code, adds nothing and is not asked to fail. Any other exit (pytest 3 or 5, exit 4 without
that line, as for a crashing `conftest.py`, a signal, a timeout) means the run itself broke and the
check fails with the output, rather than counting as a failure of the test.
The check returns the added tests that passed.
"""

from __future__ import annotations

import argparse
import os
import shlex
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

from flowchecks import _git, _testsource

DEFAULT_TIMEOUT_SECONDS = 900.0
# pytest: 1 = tests failed, 2 = interrupted (a collection error, such as a missing import).
FAILED_FIRST_EXIT_CODES = frozenset({1, 2})


class RunError(RuntimeError):
    """A test run broke in a way that proves nothing (so the check fails, never passes)."""


def added_tests(repo: str | os.PathLike[str], base: str, head: str) -> list[tuple[str, list[str]]]:
    """For each test commit of `base..head`: its sha and the IDs of the tests it adds."""
    found: list[tuple[str, list[str]]] = []
    for commit in _git.commits(repo, base, head):
        if not commit.is_test:
            continue
        parent = f"{commit.sha}^"
        ids: list[str] = []
        for path in _git.changed_files(repo, parent, commit.sha):
            if not _git.is_test_file(path):
                continue
            before = _testsource.parse(_git.show_file(repo, parent, path))
            after = _testsource.parse(_git.show_file(repo, commit.sha, path))
            names = [n for n in after.functions if n not in before.functions]
            ids += [_testsource.make_id(path, n) for n in names]
        if ids:
            found.append((commit.sha, sorted(ids)))
    return found


def check(
    repo: str | os.PathLike[str],
    base: str,
    head: str,
    test_command: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[str]:
    """The IDs of the added tests that passed at their own test commit. Empty is a pass.

    Raises RunError when a run ended in a way that is neither a pass nor a failure of the test.
    """
    plan = added_tests(repo, base, head)
    if not plan:
        return []
    passing: list[str] = []
    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="flowchecks-") as scratch:
        tree = Path(scratch) / "checkout"
        _git.git(repo, "worktree", "add", "--detach", "--force", str(tree), plan[0][0])
        try:
            for sha, ids in plan:
                _git.git(tree, "checkout", "-q", "--force", "--detach", sha)
                for test in ids:
                    _run_one(tree, test_command, test, timeout, passing, problems)
        finally:
            _git.git(repo, "worktree", "remove", "--force", str(tree))
    if problems:
        raise RunError("\n".join(problems))
    return passing


def _run_one(
    tree: Path,
    test_command: Sequence[str],
    test: str,
    timeout: float,
    passing: list[str],
    problems: list[str],
) -> None:
    try:
        result = subprocess.run(
            [*test_command, test],
            cwd=tree,
            check=False,
            timeout=timeout,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except subprocess.TimeoutExpired:
        problems.append(f"{test}: timed out after {timeout:.0f} seconds")
        return
    out = result.stdout.decode("utf-8", "replace")
    if result.returncode == 0:
        passing.append(test)
        print(out, file=sys.stderr)
    elif result.returncode in FAILED_FIRST_EXIT_CODES or _module_failed_to_import(
        test, result, out
    ):
        print(f"  {test}: exit {result.returncode}, as it should", file=sys.stderr)
    else:
        problems.append(f"{test}: the run ended with exit {result.returncode}, not 1 or 2:\n{out}")


def _module_failed_to_import(
    test: str, result: subprocess.CompletedProcess[bytes], out: str
) -> bool:
    """pytest exits 4 ("found no collectors") for a test ID whose own module fails to import.

    That is an error of the test, as the rule asks. A crashing `conftest.py` also exits 4 but
    prints no "ERROR collecting <the test's file>", so it stays an error of the run.
    """
    path = test.partition("::")[0]
    return result.returncode == 4 and f"ERROR collecting {path}" in out


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--test-command", required=True, help="split as a shell would")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    try:
        passing = check(
            args.repo, args.base, args.head, shlex.split(args.test_command), timeout=args.timeout
        )
    except RunError as error:
        print(
            "Failing first: a run broke (this is an error of the run, not a failing test):",
            file=sys.stderr,
        )
        print(error, file=sys.stderr)
        return 1
    if passing:
        print("Failing first: these new tests passed before the code existed:", file=sys.stderr)
        for test in passing:
            print(test)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
