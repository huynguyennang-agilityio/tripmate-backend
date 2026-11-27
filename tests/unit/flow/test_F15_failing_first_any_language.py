"""F15 Failing first for tests in any language, cases F15-1 to F15-5 (epic-11 spec, section 2.12).

The caller gives its test file pattern and its test command. A test file of another language
that a test commit adds is run with that command at that commit and must fail; a Python test
file keeps the per-function check of F14. The JavaScript tests run with Node's own runner,
`node --test <file>`, so `node` must be on the PATH (a missing `node` fails these tests).
F15-5 also uses small Python runners, kept outside the repository under check, for the runs
that exit 126 or 127 and for runner output that says the file holds no test.
The interface is in tests/INTERFACE.md, section "F15: Failing first in any language".
"""

from __future__ import annotations

import shlex
import stat
import sys
from pathlib import Path

import pytest

from tests.unit.flow.repo import (
    TEST_COMMAND,
    cli_test_command,
    commit,
    flowcheck,
    init_repo,
    output,
    run_cli,
)

JS_PATTERN = "tests/**/*.test.js"
JS_TEST_COMMAND = ("node", "--test")
JS_TEST_COMMAND_LINE = "node --test"
PY_PATTERN = "test_*.py"

# A command no machine has: the run cannot start.
MISSING_COMMAND = ("tripmate-f15-no-such-test-runner",)
MISSING_COMMAND_LINE = "tripmate-f15-no-such-test-runner"

JS_BASE_FILES = {
    "README.md": "# Probe repository\n",
    "package.json": '{\n  "name": "probe",\n  "private": true,\n  "type": "commonjs"\n}\n',
    "calc.js": (
        "function add(a, b) {\n  throw new Error('not implemented');\n}\n\n"
        "module.exports = { add };\n"
    ),
}

JS_CODE = "function add(a, b) {\n  return a + b;\n}\n\nmodule.exports = { add };\n"

# Fails at the test commit: `add` still throws.
JS_FAILING_TEST = """const test = require('node:test');
const assert = require('node:assert');
const { add } = require('../../calc.js');

test('adds two numbers', () => {
  assert.strictEqual(add(2, 3), 5);
});
"""

# Passes at the test commit: it needs nothing the code commit adds.
JS_PASSING_TEST = """const test = require('node:test');
const assert = require('node:assert');

test('already passes', () => {
  assert.strictEqual('a' + 'b', 'ab');
});
"""

JS_PASSING_TEST_CHANGED = """const test = require('node:test');
const assert = require('node:assert');

test('already passes', () => {
  assert.strictEqual('a' + 'b' + 'c', 'abc');
});
"""

JS_FILE = "tests/unit/calc.test.js"
JS_OLD_FILE = "tests/unit/old.test.js"

# One new function that fails (`add` raises) and one that passes, in one new Python file.
PY_MIXED_TEST = """from calc import add


def test_adds_two_numbers():
    assert add(2, 3) == 5


def test_already_passes():
    assert "a" + "b" == "ab"
"""

PY_CODE = "def add(a, b):\n    return a + b\n"


def _js_repo(tmp_path: Path, files: dict[str, str] | None = None) -> tuple[Path, str]:
    return init_repo(tmp_path / "repo", {**JS_BASE_FILES, **(files or {})})


def _cli(
    repo: Path, base: str, head: str, test_command: str, pattern: str, *extra: str
) -> tuple[int, str]:
    result = run_cli(
        "failing_first",
        "--repo",
        str(repo),
        "--base",
        base,
        "--head",
        head,
        "--test-command",
        test_command,
        "--test-file-pattern",
        pattern,
        *extra,
    )
    return result.returncode, output(result)


def test_F15_1_an_added_javascript_test_file_that_passes_at_its_test_commit_fails_the_check_and_is_named(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    commit(repo, {JS_FILE: JS_PASSING_TEST}, "F99 Tests", test=True)
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")

    passing = failing_first.check(repo, base, head, JS_TEST_COMMAND, test_file_pattern=JS_PATTERN)
    assert JS_FILE in passing

    code, out = _cli(repo, base, head, JS_TEST_COMMAND_LINE, JS_PATTERN)
    assert code != 0, out
    assert JS_FILE in out


def test_F15_2_an_added_javascript_test_file_that_fails_at_its_test_commit_passes_the_check(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    commit(repo, {JS_FILE: JS_FAILING_TEST}, "F99 Tests", test=True)
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")

    assert (
        failing_first.check(repo, base, head, JS_TEST_COMMAND, test_file_pattern=JS_PATTERN) == []
    )

    code, out = _cli(repo, base, head, JS_TEST_COMMAND_LINE, JS_PATTERN)
    assert code == 0, out


def test_F15_3_a_test_commit_that_only_changes_an_existing_javascript_test_file_is_not_asked_to_fail(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path, {JS_OLD_FILE: JS_PASSING_TEST})
    # A correction after the code: the changed file still passes, and that is allowed.
    head = commit(repo, {JS_OLD_FILE: JS_PASSING_TEST_CHANGED}, "F99 Test fix", test=True)

    assert (
        failing_first.check(repo, base, head, JS_TEST_COMMAND, test_file_pattern=JS_PATTERN) == []
    )

    code, out = _cli(repo, base, head, JS_TEST_COMMAND_LINE, JS_PATTERN)
    assert code == 0, out


def test_F15_4_a_python_test_function_added_by_a_test_commit_is_checked_per_function_as_in_F14(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, {"tests/test_calc.py": PY_MIXED_TEST}, "F99 Tests", test=True)
    head = commit(repo, {"calc.py": PY_CODE}, "F99 Code")

    passing = failing_first.check(repo, base, head, TEST_COMMAND, test_file_pattern=PY_PATTERN)
    # Per function: the passing function is named, the failing one and the file are not.
    assert passing == ["tests/test_calc.py::test_already_passes"]

    code, out = _cli(repo, base, head, cli_test_command(), PY_PATTERN)
    assert code != 0, out
    assert "tests/test_calc.py::test_already_passes" in out
    assert "tests/test_calc.py::test_adds_two_numbers" not in out


def test_F15_5_a_test_command_that_cannot_start_fails_the_check_as_an_error_of_the_run_and_names_the_file(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    commit(repo, {JS_FILE: JS_FAILING_TEST}, "F99 Tests", test=True)
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")

    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(repo, base, head, MISSING_COMMAND, test_file_pattern=JS_PATTERN)
    assert JS_FILE in str(raised.value)

    code, out = _cli(repo, base, head, MISSING_COMMAND_LINE, JS_PATTERN)
    assert code != 0, out
    assert JS_FILE in out
    assert "error of the run" in out


# The caller's no-test pattern for `node --test` (EPIC-11 spec, section 2.12).
NO_TEST_PATTERN = "^# tests 0$"

# A JavaScript test file that holds no test.
JS_NO_TEST = "const test = require('node:test');\n"

# A runner that prints what a TAP runner prints for a file with no test, and exits 0.
NO_TEST_RUNNER = """import sys

print("TAP version 13")
print("1..0")
print("# tests 0")
print("# suites 0")
print("# pass 0")
print("# fail 0")
sys.exit(0)
"""

# A runner that prints a TAP summary and exits with the code it is given.
EXIT_RUNNER = """import sys

print("TAP version 13")
print("# tests 1")
sys.exit(int(sys.argv[1]))
"""


def _python_runner(tmp_path: Path, name: str, text: str, *args: str) -> tuple[tuple[str, ...], str]:
    """A test command that runs a Python script kept outside the repository under check."""
    script = tmp_path / "runners" / name
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(text, encoding="utf-8")
    command = (sys.executable, str(script), *args)
    return command, shlex.join(command)


def _js_test_commit(tmp_path: Path, test_text: str) -> tuple[Path, str, str]:
    repo, base = _js_repo(tmp_path)
    commit(repo, {JS_FILE: test_text}, "F99 Tests", test=True)
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")
    return repo, base, head


def test_F15_5_a_test_command_that_is_a_file_that_cannot_be_run_fails_the_check_as_an_error_of_the_run(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _js_test_commit(tmp_path, JS_FAILING_TEST)
    # A file with no execute permission: a shell gives exit 126 for it.
    runner = tmp_path / "runners" / "not-executable-runner"
    runner.parent.mkdir(parents=True, exist_ok=True)
    runner.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    runner.chmod(stat.S_IRUSR | stat.S_IWUSR)

    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(repo, base, head, (str(runner),), test_file_pattern=JS_PATTERN)
    assert JS_FILE in str(raised.value)

    code, out = _cli(repo, base, head, shlex.quote(str(runner)), JS_PATTERN)
    assert code != 0, out
    assert JS_FILE in out
    assert "error of the run" in out


@pytest.mark.parametrize("exit_code", [126, 127])
def test_F15_5_a_run_that_exits_126_or_127_fails_the_check_as_an_error_of_the_run_and_names_the_file(
    tmp_path: Path, exit_code: int
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _js_test_commit(tmp_path, JS_FAILING_TEST)
    command, command_line = _python_runner(tmp_path, "exit_runner.py", EXIT_RUNNER, str(exit_code))

    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(repo, base, head, command, test_file_pattern=JS_PATTERN)
    assert JS_FILE in str(raised.value)

    code, out = _cli(repo, base, head, command_line, JS_PATTERN)
    assert code != 0, out
    assert JS_FILE in out
    assert "error of the run" in out


def test_F15_5_runner_output_that_matches_the_no_test_pattern_fails_the_check_as_an_error_of_the_run(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _js_test_commit(tmp_path, JS_NO_TEST)
    command, command_line = _python_runner(tmp_path, "no_test_runner.py", NO_TEST_RUNNER)

    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(
            repo,
            base,
            head,
            command,
            test_file_pattern=JS_PATTERN,
            no_test_pattern=NO_TEST_PATTERN,
        )
    assert JS_FILE in str(raised.value)

    code, out = _cli(
        repo, base, head, command_line, JS_PATTERN, "--no-test-pattern", NO_TEST_PATTERN
    )
    assert code != 0, out
    assert JS_FILE in out
    assert "error of the run" in out


def test_F15_5_a_failing_javascript_test_whose_output_does_not_match_the_no_test_pattern_passes(
    tmp_path: Path,
) -> None:
    # Control for the no-test half: Node prints `# tests 1` for this file, which the pattern,
    # matched per line, does not match; the file fails at its test commit, so the check passes.
    failing_first = flowcheck("failing_first")
    repo, base, head = _js_test_commit(tmp_path, JS_FAILING_TEST)

    assert (
        failing_first.check(
            repo,
            base,
            head,
            JS_TEST_COMMAND,
            test_file_pattern=JS_PATTERN,
            no_test_pattern=NO_TEST_PATTERN,
        )
        == []
    )

    code, out = _cli(
        repo, base, head, JS_TEST_COMMAND_LINE, JS_PATTERN, "--no-test-pattern", NO_TEST_PATTERN
    )
    assert code == 0, out
