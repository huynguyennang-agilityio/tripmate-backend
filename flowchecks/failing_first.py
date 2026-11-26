"""Failing first: a new test of the test commit must fail before the code exists.

The tests the test commits add are run at the last test commit; each must fail or error.
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


def added_tests(repo: str | os.PathLike[str], base: str, head: str) -> tuple[str | None, list[str]]:
    """The last test commit of `base..head`, and the IDs of the tests there that `base` lacks."""
    test_commits = [c for c in _git.commits(repo, base, head) if c.is_test]
    if not test_commits:
        return None, []
    last = test_commits[-1].sha
    start = _git.merge_base(repo, base, head)
    ids: list[str] = []
    for path in _git.changed_files(repo, start, last):
        if not _git.is_test_file(path):
            continue
        before = _testsource.parse(_git.show_file(repo, start, path))
        after = _testsource.parse(_git.show_file(repo, last, path))
        ids += [_testsource.make_id(path, n) for n in after.functions if n not in before.functions]
    return last, sorted(ids)


def check(
    repo: str | os.PathLike[str],
    base: str,
    head: str,
    test_command: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[str]:
    """The IDs of the added tests that passed at the last test commit. Empty is a pass."""
    last, ids = added_tests(repo, base, head)
    if last is None or not ids:
        return []
    passing: list[str] = []
    with tempfile.TemporaryDirectory(prefix="flowchecks-") as scratch:
        tree = Path(scratch) / "checkout"
        _git.git(repo, "worktree", "add", "--detach", "--force", str(tree), last)
        try:
            for test in ids:
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
                    print(f"  {test}: timed out, counted as failing", file=sys.stderr)
                    continue
                if result.returncode == 0:
                    passing.append(test)
                    print(result.stdout.decode("utf-8", "replace"), file=sys.stderr)
                else:
                    print(f"  {test}: exit {result.returncode}, as it should", file=sys.stderr)
        finally:
            _git.git(repo, "worktree", "remove", "--force", str(tree))
    return passing


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--test-command", required=True, help="split as a shell would")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args(argv)
    passing = check(
        args.repo, args.base, args.head, shlex.split(args.test_command), timeout=args.timeout
    )
    if passing:
        print("Failing first: these new tests passed before the code existed:", file=sys.stderr)
        for test in passing:
            print(test)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
