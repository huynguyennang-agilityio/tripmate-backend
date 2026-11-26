"""Test lock: a commit that is not a test commit never weakens a test the test commits added.

Commits are read one by one, oldest first. A test commit adds tests to the lock (and may change
or delete them). Any other commit that changes a locked test's function (body or decorators),
deletes it or its file, or skips it (on the function or for the whole file) breaks it. A new test
function added to a locked file by such a commit is not locked and does not break anything.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence

from flowchecks import _git, _testsource


def check(repo: str | os.PathLike[str], base: str, head: str) -> list[str]:
    """The IDs of the locked tests a later commit that is not a test commit broke."""
    locked: dict[str, str] = {}  # test ID -> fingerprint at the last test commit that set it
    broken: set[str] = set()
    for commit in _git.commits(repo, base, head):
        parent = f"{commit.sha}^"
        for path in _git.changed_files(repo, parent, commit.sha):
            if not _git.is_test_file(path):
                continue
            before = _testsource.parse(_git.show_file(repo, parent, path))
            after = _testsource.parse(_git.show_file(repo, commit.sha, path))
            if commit.is_test:
                for name, fingerprint in after.functions.items():
                    test = _testsource.make_id(path, name)
                    if name not in before.functions or test in locked:
                        locked[test] = fingerprint
                for name in before.functions.keys() - after.functions.keys():
                    locked.pop(_testsource.make_id(path, name), None)
                continue
            newly_skipped = after.skipped and not before.skipped
            for name in before.functions.keys() | after.functions.keys():
                test = _testsource.make_id(path, name)
                if test not in locked:
                    continue
                gone = name not in after.functions
                if gone or after.functions[name] != locked[test] or newly_skipped:
                    broken.add(test)
    return sorted(broken)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    broken = check(args.repo, args.base, args.head)
    if broken:
        print(
            "Test lock: a commit without the trailer `Agent: test` changed, skipped, "
            "or deleted these tests:",
            file=sys.stderr,
        )
        for test in broken:
            print(test)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
