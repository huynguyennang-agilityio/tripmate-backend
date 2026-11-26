"""F15 Failing first for tests in any language, cases F15-1 to F15-5 (epic-11 spec, section 2.12).

The caller gives its test file pattern and its test command. A test file of another language
that a test commit adds is run with that command at that commit and must fail; a Python test
file keeps the per-function check of F14. The JavaScript tests run with Node's own runner,
`node --test <file>`, so `node` must be on the PATH (a missing `node` fails these tests).
The interface is in tests/INTERFACE.md, section "F15: Failing first in any language".
"""

from __future__ import annotations

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


def _cli(repo: Path, base: str, head: str, test_command: str, pattern: str) -> tuple[int, str]:
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
