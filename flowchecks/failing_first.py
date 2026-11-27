"""Failing first: a new test of a test commit must fail before the code exists.

Each test commit of `base..head` is checked out, in order, and the tests that commit adds (IDs
its parent's tree lacks) are run there; each must fail (pytest exit 1) or error (exit 2, or exit 4
when the output has `ERROR collecting <the test's own file>`, which is how pytest answers a test ID
whose module fails to import). A test commit that only changes existing tests, such as a correction
after the code, adds nothing and is not asked to fail. Any other exit (pytest 3 or 5, exit 4 without
that line, as for a crashing `conftest.py`, a signal, a timeout) means the run itself broke and the
check fails with the output, rather than counting as a failure of the test.
The check returns the added tests that passed.

The caller's test file pattern (`test_file_pattern`, default `test_*.py`) says which files are test
files. A matching `.py` file is checked per function, as above. Any other matching file that a test
commit adds (it is absent at the parent) is run whole, as `test_command + [path]`, and must exit
non-zero; its ID is its path. A file the commit only changes is not run. For such a file the run
itself is in error (a RunError) when the command cannot start, exits 126 or 127, or prints output
that matches the caller's `no_test_pattern` (the file holds no test), whatever the exit code.
"""

from __future__ import annotations

import argparse
import contextlib
import fnmatch
import os
import re
import shlex
import signal
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath

from flowchecks import _git, _testsource

DEFAULT_TIMEOUT_SECONDS = 900.0
# pytest: 1 = tests failed, 2 = interrupted (a collection error, such as a missing import).
FAILED_FIRST_EXIT_CODES = frozenset({1, 2})
DEFAULT_TEST_FILE_PATTERN = "test_*.py"
# A shell's exit codes for a command that cannot be run (126) or is not found (127).
CANNOT_RUN_EXIT_CODES = frozenset({126, 127})
# How many trailing lines of a run's output an error message keeps.
OUTPUT_TAIL_LINES = 200


class PatternError(ValueError):
    """A pattern the caller gave is not usable (so the check fails, never passes)."""


def validate_patterns(test_file_pattern: str, no_test_pattern: str | None) -> None:
    """Raise PatternError for a pattern that could match nothing or is not a regex."""
    if test_file_pattern.startswith(("/", "./")):
        raise PatternError(
            f"the test file pattern {test_file_pattern!r} starts with `/` or `./`: paths are "
            "matched from the repository root with no such prefix (write `tests/**/*.test.js`)"
        )
    if "{" in test_file_pattern or "}" in test_file_pattern:
        raise PatternError(
            f"the test file pattern {test_file_pattern!r} uses braces, which are not supported "
            "(use `[...]` or a simpler pattern such as `*.test.*`)"
        )
    if no_test_pattern:
        try:
            re.compile(no_test_pattern, re.MULTILINE)
        except re.error as error:
            raise PatternError(
                f"the no-test pattern is not a valid regular expression: {error}"
            ) from error


def _tail(out: str) -> str:
    lines = out.splitlines()
    if len(lines) <= OUTPUT_TAIL_LINES:
        return out
    return f"... ({len(lines) - OUTPUT_TAIL_LINES} earlier lines left out)\n" + "\n".join(
        lines[-OUTPUT_TAIL_LINES:]
    )


def _run(command: Sequence[str], cwd: Path, timeout: float) -> subprocess.CompletedProcess[bytes]:
    """Run `command` in its own process group; on timeout kill the whole group, then raise."""
    proc = subprocess.Popen(
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        stdout, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGKILL)
        proc.communicate()
        raise
    return subprocess.CompletedProcess(command, proc.returncode, stdout)


class RunError(RuntimeError):
    """A test run broke in a way that proves nothing (so the check fails, never passes)."""


def _glob_regex(pattern: str) -> re.Pattern[str]:
    """A path pattern as a regex: `*` and `?` stay in one folder, `**` spans folders."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif pattern[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pattern[i] == "?":
            out.append("[^/]")
            i += 1
        elif pattern[i] == "[" and (end := pattern.find("]", i + 2)) != -1:
            body = pattern[i + 1 : end]
            if body[0] in "!^":
                body = "^/" + re.sub(r"([\\\]\[])", r"\\\1", body[1:])
            else:
                body = re.sub(r"([\\\]\[^])", r"\\\1", body)
            out.append(f"[{body}]")
            i = end + 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def matches_pattern(path: str, pattern: str) -> bool:
    """A pattern without `/` matches the file name; one with `/` matches the whole path."""
    if "/" not in pattern:
        return fnmatch.fnmatchcase(PurePosixPath(path).name, pattern)
    return _glob_regex(pattern).fullmatch(path) is not None


def added_tests(
    repo: str | os.PathLike[str],
    base: str,
    head: str,
    test_file_pattern: str = DEFAULT_TEST_FILE_PATTERN,
) -> list[tuple[str, list[str]]]:
    """For each test commit of `base..head`: its sha and the IDs of the tests it adds.

    A Python test file gives one ID per added function (`<path>::<function>`); a test file of
    another language that the commit adds gives its path.
    """
    found: list[tuple[str, list[str]]] = []
    for commit in _git.commits(repo, base, head):
        if not commit.is_test:
            continue
        parent = f"{commit.sha}^"
        ids: list[str] = []
        renamed: set[str] | None = None
        matched = 0
        for path in _git.changed_files(repo, parent, commit.sha):
            if not matches_pattern(path, test_file_pattern):
                continue
            matched += 1
            before_text = _git.show_file(repo, parent, path)
            after_text = _git.show_file(repo, commit.sha, path)
            if path.endswith(".py"):
                before = _testsource.parse(before_text)
                after = _testsource.parse(after_text)
                names = [n for n in after.functions if n not in before.functions]
                ids += [_testsource.make_id(path, n) for n in names]
            elif before_text is None and after_text is not None:
                if renamed is None:
                    renamed = _renamed_paths(repo, parent, commit.sha)
                if path not in renamed:
                    ids.append(path)
        print(
            f"  commit {commit.sha[:7]}: {matched} test file(s) match {test_file_pattern!r}",
            file=sys.stderr,
        )
        if ids:
            found.append((commit.sha, sorted(ids)))
    return found


def _renamed_paths(repo: str | os.PathLike[str], old: str, new: str) -> set[str]:
    """New paths of the files that git sees as renamed or moved between two revisions."""
    out = _git.git(repo, "diff", "--name-status", "-M", "-z", old, new)
    fields = out.decode("utf-8", "replace").split("\0")
    renamed: set[str] = set()
    i = 0
    while i < len(fields) and fields[i]:
        status = fields[i]
        if status[0] in "RC":
            renamed.add(fields[i + 2])
            i += 3
        else:
            i += 2
    return renamed


def check(
    repo: str | os.PathLike[str],
    base: str,
    head: str,
    test_command: Sequence[str],
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    test_file_pattern: str = DEFAULT_TEST_FILE_PATTERN,
    no_test_pattern: str | None = None,
) -> list[str]:
    """The IDs of the added tests that passed at their own test commit. Empty is a pass.

    Raises RunError when a run ended in a way that is neither a pass nor a failure of the test.
    """
    validate_patterns(test_file_pattern, no_test_pattern)
    plan = added_tests(repo, base, head, test_file_pattern)
    no_test = re.compile(no_test_pattern, re.MULTILINE) if no_test_pattern else None
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
                    _run_one(tree, test_command, test, timeout, passing, problems, no_test)
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
    no_test: re.Pattern[str] | None = None,
) -> None:
    whole_file = "::" not in test
    try:
        result = _run([*test_command, test], tree, timeout)
    except subprocess.TimeoutExpired:
        problems.append(f"{test}: timed out after {timeout:.0f} seconds")
        return
    except OSError as error:
        problems.append(f"{test}: the test command cannot start: {error}")
        return
    out = result.stdout.decode("utf-8", "replace")
    if whole_file:
        _judge_file(test, result.returncode, out, passing, problems, no_test)
    elif result.returncode == 0:
        passing.append(test)
        print(out, file=sys.stderr)
    elif result.returncode in FAILED_FIRST_EXIT_CODES or _module_failed_to_import(
        test, result, out
    ):
        # Failing first, as it should: nothing is printed, so the output names only the tests
        # that passed.
        pass
    else:
        problems.append(
            f"{test}: the run ended with exit {result.returncode}, not 1 or 2:\n{_tail(out)}"
        )


def _judge_file(
    test: str,
    returncode: int,
    out: str,
    passing: list[str],
    problems: list[str],
    no_test: re.Pattern[str] | None,
) -> None:
    """A test file of another language: non-zero is failing first, but an error of the run is not."""
    if returncode in CANNOT_RUN_EXIT_CODES:
        problems.append(
            f"{test}: the test command could not run (exit {returncode}):\n{_tail(out)}"
        )
    elif returncode < 0:
        problems.append(f"{test}: the run was killed by signal {-returncode}:\n{_tail(out)}")
    elif no_test is not None and no_test.search(out):
        problems.append(
            f"{test}: the run found no test in the file (no-test pattern matched):\n{_tail(out)}"
        )
    elif returncode == 0:
        passing.append(test)
        print(out, file=sys.stderr)
    # Any other non-zero exit is failing first, as it should: nothing is printed.


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
    parser.add_argument("--test-file-pattern", default=DEFAULT_TEST_FILE_PATTERN)
    parser.add_argument(
        "--no-test-pattern", default="", help="regex; output that matches is an error of the run"
    )
    args = parser.parse_args(argv)
    try:
        passing = check(
            args.repo,
            args.base,
            args.head,
            shlex.split(args.test_command),
            timeout=args.timeout,
            test_file_pattern=args.test_file_pattern or DEFAULT_TEST_FILE_PATTERN,
            no_test_pattern=args.no_test_pattern or None,
        )
    except PatternError as error:
        print(f"Failing first: {error}", file=sys.stderr)
        return 1
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
