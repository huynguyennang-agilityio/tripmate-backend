"""Test lock: a commit that is not a test commit never weakens what the test commits added.

Commits are read one by one, oldest first. A test commit adds tests to the lock (and may change
or delete them). Any other commit that changes a locked test (its function or method, with the
module and class context it depends on), deletes it or its file, or skips it (on the function,
the class, or the whole file) breaks it. A new test function added to a locked file by such a
commit is not locked. Every file under `tests/` that is not a test file (`conftest.py`, helpers,
data) is locked the same way once a test commit adds or changes it. After the walk, the head is
compared with the last locked state too, so a merge or any other path cannot slip a change in.
Any `conftest.py`, at any depth, that a commit without the trailer adds, changes, or deletes
breaks the lock, locked or not. A test commit that changes a path outside `tests/` is itself a failure (the trailer can be forged).
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import PurePosixPath

from flowchecks import _git, _testsource


def check(repo: str | os.PathLike[str], base: str, head: str) -> list[str]:
    """What a later commit that is not a test commit broke. Empty is a pass.

    Entries are locked test IDs (`path::function`), locked support files (a path), or a test
    commit that changes a path outside `tests/` (`commit <sha>: ...`).
    """
    locked: dict[str, str] = {}  # test ID -> fingerprint at the last test commit that set it
    support: dict[str, str | None] = {}  # support file -> its text at the last test commit
    broken: set[str] = set()
    for commit in _git.commits(repo, base, head):
        parent = f"{commit.sha}^"
        paths = _git.changed_files(repo, parent, commit.sha)
        if commit.is_test:
            outside = sorted(p for p in paths if not _git.in_test_folder(p))
            if outside:
                broken.add(
                    f"commit {commit.sha[:12]} has the trailer `Agent: test` but changes "
                    f"{', '.join(outside)} outside tests/"
                )
        for path in paths:
            if not commit.is_test and PurePosixPath(path).name == "conftest.py":
                broken.add(path)  # a hook can deselect or skip any test, at any depth
                continue
            if _git.is_support_file(path):
                text = _git.show_file(repo, commit.sha, path)
                if commit.is_test:
                    support[path] = text
                elif path in support and text != support[path]:
                    broken.add(path)
                continue
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
            for name in before.functions.keys() | after.functions.keys():
                test = _testsource.make_id(path, name)
                if test in locked and after.functions.get(name) != locked[test]:
                    broken.add(test)
    broken |= _broken_at_head(repo, head, locked, support)
    return sorted(broken)


def _broken_at_head(
    repo: str | os.PathLike[str],
    head: str,
    locked: dict[str, str],
    support: dict[str, str | None],
) -> set[str]:
    """Locked tests and files that differ at `head` from their last locked state."""
    broken: set[str] = set()
    parsed: dict[str, _testsource.ModuleTests] = {}
    for test, fingerprint in locked.items():
        path, _, name = test.partition("::")
        if path not in parsed:
            parsed[path] = _testsource.parse(_git.show_file(repo, head, path))
        if parsed[path].functions.get(name) != fingerprint:
            broken.add(test)
    for path, text in support.items():
        if _git.show_file(repo, head, path) != text:
            broken.add(path)
    return broken


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)
    broken = check(args.repo, args.base, args.head)
    if broken:
        print(
            "Test lock: these tests or test files were changed, skipped, or deleted by a commit "
            "without the trailer `Agent: test` (or a test commit changed more than tests/):",
            file=sys.stderr,
        )
        for entry in broken:
            print(entry)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
