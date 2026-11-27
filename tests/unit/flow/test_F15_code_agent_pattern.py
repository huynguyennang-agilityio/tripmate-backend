"""F15 code agent: the test file pattern matching of flowchecks.failing_first."""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from tests.unit.flow.repo import commit, flowcheck, init_repo, output, run_cli

FILE = "tests/unit/calc.test.js"
PATTERN = "tests/**/*.test.js"


def _python(code: str) -> tuple[str, ...]:
    """A test command that runs `code` (the test path is appended as an argument)."""
    return (sys.executable, "-c", code)


def _repo_with_added_file(tmp_path: Path) -> tuple[Path, str, str]:
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, {FILE: "x\n"}, "F99 Tests", test=True)
    head = commit(repo, {"calc.js": "y\n"}, "F99 Code")
    return repo, base, head


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("tests/unit/test_calc.py", "test_*.py", True),
        ("test_calc.py", "test_*.py", True),
        ("tests/unit/calc_test.py", "test_*.py", False),
        ("tests/unit/calc.test.js", "tests/**/*.test.js", True),
        ("tests/calc.test.js", "tests/**/*.test.js", True),
        ("tests/a/b/calc.test.js", "tests/**/*.test.js", True),
        ("src/tests/calc.test.js", "tests/**/*.test.js", False),
        ("tests/calc.test.ts", "tests/**/*.test.js", False),
        ("tests/calc.test.js", "tests/*.test.js", True),
        ("tests/a/calc.test.js", "tests/*.test.js", False),
        ("tests/a/calc.test.js", "**/*.test.js", True),
        ("calc.test.js", "**/*.test.js", True),
        ("tests/calc.test.js", "*.test.js", True),
    ],
)
def test_F15_pattern_matching(path: str, pattern: str, expected: bool) -> None:
    failing_first = flowcheck("failing_first")
    assert failing_first.matches_pattern(path, pattern) is expected


@pytest.mark.parametrize(
    ("path", "pattern", "expected"),
    [
        ("tests/a1.test.js", "tests/a[0-9].test.js", True),
        ("tests/ab.test.js", "tests/a[0-9].test.js", False),
        ("tests/ax.test.js", "tests/a[!0-9].test.js", True),
        ("tests/a1.test.js", "tests/a[!0-9].test.js", False),
        ("a1.test.js", "a[0-9].test.js", True),
    ],
)
def test_F15_character_classes_agree_in_both_branches(
    path: str, pattern: str, expected: bool
) -> None:
    failing_first = flowcheck("failing_first")
    assert failing_first.matches_pattern(path, pattern) is expected


@pytest.mark.parametrize(
    "pattern",
    ["./tests/*.test.js", "/tests/*.test.js", "tests/*.{test,spec}.ts", "{a,b}.test.js"],
)
def test_F15_a_pattern_that_cannot_match_is_rejected_with_a_message(
    tmp_path: Path, pattern: str
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _repo_with_added_file(tmp_path)
    with pytest.raises(failing_first.PatternError):
        failing_first.check(repo, base, head, _python("pass"), test_file_pattern=pattern)

    result = run_cli(
        "failing_first", "--repo", str(repo), "--base", base, "--head", head,
        "--test-command", "true", f"--test-file-pattern={pattern}",
    )  # fmt: skip
    assert result.returncode != 0
    assert "test file pattern" in output(result)


def test_F15_an_invalid_no_test_pattern_is_rejected_with_a_message(tmp_path: Path) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _repo_with_added_file(tmp_path)
    with pytest.raises(failing_first.PatternError):
        failing_first.check(
            repo, base, head, _python("pass"), test_file_pattern=PATTERN, no_test_pattern="(oops"
        )

    result = run_cli(
        "failing_first", "--repo", str(repo), "--base", base, "--head", head,
        "--test-command", "true", f"--test-file-pattern={PATTERN}", "--no-test-pattern=-(oops",
    )  # fmt: skip
    assert result.returncode != 0
    out = output(result)
    assert "not a valid regular expression" in out
    assert "Traceback" not in out


def test_F15_the_count_of_matched_files_is_printed_per_test_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _repo_with_added_file(tmp_path)
    failing_first.check(repo, base, head, _python("raise SystemExit(1)"), test_file_pattern=PATTERN)
    assert "1 test file(s) match" in capsys.readouterr().err


def test_F15_a_renamed_non_python_test_file_is_not_asked_to_fail(tmp_path: Path) -> None:
    failing_first = flowcheck("failing_first")
    text = "const test = require('node:test');\ntest('ok', () => {});\n"
    repo, base = init_repo(tmp_path / "repo", {"tests/a.test.js": text})
    commit(repo, {"tests/a.test.js": None, "tests/unit/a.test.js": text}, "F99 Move", test=True)
    head = commit(repo, {"calc.js": "y\n"}, "F99 Code")
    # The command passes (exit 0): an added file would be named, a moved one is not run.
    assert failing_first.check(repo, base, head, _python("pass"), test_file_pattern=PATTERN) == []


def test_F15_a_run_killed_by_a_signal_is_an_error_of_the_run_and_names_the_file(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _repo_with_added_file(tmp_path)
    kill = _python("import os, signal; os.kill(os.getpid(), signal.SIGKILL)")
    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(repo, base, head, kill, test_file_pattern=PATTERN)
    assert FILE in str(raised.value)
    assert "signal" in str(raised.value)


def test_F15_an_exit_code_of_128_or_more_from_a_wrapper_counts_as_failing(tmp_path: Path) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _repo_with_added_file(tmp_path)
    assert (
        failing_first.check(
            repo, base, head, _python("raise SystemExit(137)"), test_file_pattern=PATTERN
        )
        == []
    )


def test_F15_a_timeout_kills_the_runners_children_and_names_the_file(tmp_path: Path) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _repo_with_added_file(tmp_path)
    forking = _python("import subprocess, time; subprocess.Popen(['sleep', '60']); time.sleep(60)")
    started = time.monotonic()
    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(repo, base, head, forking, test_file_pattern=PATTERN, timeout=1)
    assert time.monotonic() - started < 30
    assert FILE in str(raised.value)
    assert "timed out" in str(raised.value)
