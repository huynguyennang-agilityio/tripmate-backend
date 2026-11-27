"""F16 Failing first sets up each test commit, cases F16-1 to F16-4 (epic-11 spec, section 2.12).

Failing first runs the caller's setup command (through `bash -c`) once in the worktree of each
test commit, a fresh checkout of that commit, before the test files that commit adds. A setup
command that breaks is an error of the run that names the commit. With no setup command,
failing first runs as in F15. The JavaScript tests run with `node --test`, as in F15, so `node`
must be on the PATH (a missing `node` fails these tests).
The interface is in tests/INTERFACE.md, section "F16: Failing first sets up each test commit".
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path

import pytest

from tests.unit.flow.repo import commit, flowcheck, git, init_repo, output, run_cli

JS_PATTERN = "tests/**/*.test.js"
JS_TEST_COMMAND = ("node", "--test")
JS_TEST_COMMAND_LINE = "node --test"

JS_BASE_FILES = {
    "README.md": "# Probe repository\n",
    ".gitignore": "node_modules/\n",
    "package.json": '{\n  "name": "probe",\n  "private": true,\n  "type": "commonjs"\n}\n',
    "calc.js": (
        "function add(a, b) {\n  throw new Error('not implemented');\n}\n\n"
        "module.exports = { add };\n"
    ),
}

JS_CODE = "function add(a, b) {\n  return a + b;\n}\n\nmodule.exports = { add };\n"

# Fails at its test commit: `add` still throws.
JS_FAILING_TEST = """const test = require('node:test');
const assert = require('node:assert');
const { add } = require('../../calc.js');

test('adds two numbers', () => {
  assert.strictEqual(add(2, 3), 5);
});
"""

# Passes at its test commit: it needs nothing the code commit adds.
JS_PASSING_TEST = """const test = require('node:test');
const assert = require('node:assert');

test('already passes', () => {
  assert.strictEqual('a' + 'b', 'ab');
});
"""

# A program no machine has.
MISSING_SETUP = "tripmate-f16-no-such-setup-tool"


def _js_repo(tmp_path: Path, files: dict[str, str] | None = None) -> tuple[Path, str]:
    return init_repo(tmp_path / "repo", {**JS_BASE_FILES, **(files or {})})


def _cli(repo: Path, base: str, head: str, test_command: str, *extra: str) -> tuple[int, str]:
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
        JS_PATTERN,
        *extra,
    )
    return result.returncode, output(result)


# --- F16-1 -----------------------------------------------------------------------------------

# The test runner the test needs, as `npm ci` would install `node_modules/.bin/vitest`: only the
# setup command writes it, in the worktree it runs in; it is never committed.
RUNNER = "node_modules/.bin/runner"
WRITE_RUNNER = (
    "mkdir -p node_modules/.bin"
    " && printf '#!/bin/sh\\nexec node --test \"$@\"\\n' > node_modules/.bin/runner"
    " && chmod +x node_modules/.bin/runner"
)


def test_F16_1_the_setup_command_runs_in_the_test_commits_worktree_first_and_the_check_passes(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    commit(repo, {"tests/unit/calc.test.js": JS_FAILING_TEST}, "F99 Tests", test=True)
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")

    # Without the setup, `node_modules/.bin/runner` does not exist and the run cannot start.
    # With it, the added test runs and fails only on its assertion, so the check passes.
    assert (
        failing_first.check(
            repo,
            base,
            head,
            (RUNNER,),
            test_file_pattern=JS_PATTERN,
            setup_command=WRITE_RUNNER,
        )
        == []
    )
    # It ran in the test commit's worktree, not in the caller's repository.
    assert not (repo / "node_modules").exists()

    code, out = _cli(repo, base, head, RUNNER, "--setup-command", WRITE_RUNNER)
    assert code == 0, out
    assert not (repo / "node_modules").exists()


# --- F16-2 -----------------------------------------------------------------------------------

SETUP_OK = "exit 0\n"
SETUP_BROKEN = "echo 'setup broke' >&2\nexit 4\n"


def test_F16_2_a_setup_command_that_exits_non_zero_is_an_error_of_the_run_that_names_the_commit(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path, {"tests/setup.sh": SETUP_OK})
    first = commit(repo, {"tests/unit/a.test.js": JS_FAILING_TEST}, "F99 Tests 1", test=True)
    second = commit(
        repo,
        {"tests/setup.sh": SETUP_BROKEN, "tests/unit/b.test.js": JS_FAILING_TEST},
        "F99 Tests 2",
        test=True,
    )
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")

    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(
            repo,
            base,
            head,
            JS_TEST_COMMAND,
            test_file_pattern=JS_PATTERN,
            setup_command="sh tests/setup.sh",
        )
    assert second in str(raised.value)
    assert first not in str(raised.value)

    code, out = _cli(repo, base, head, JS_TEST_COMMAND_LINE, "--setup-command", "sh tests/setup.sh")
    assert code != 0, out
    assert second in out
    assert "error of the run" in out


def test_F16_2_a_setup_command_that_cannot_be_found_is_an_error_of_the_run_that_names_the_commit(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    tests = commit(repo, {"tests/unit/calc.test.js": JS_FAILING_TEST}, "F99 Tests", test=True)
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")

    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(
            repo,
            base,
            head,
            JS_TEST_COMMAND,
            test_file_pattern=JS_PATTERN,
            setup_command=MISSING_SETUP,
        )
    assert tests in str(raised.value)

    code, out = _cli(repo, base, head, JS_TEST_COMMAND_LINE, "--setup-command", MISSING_SETUP)
    assert code != 0, out
    assert tests in out
    assert "error of the run" in out


# --- F16-3 -----------------------------------------------------------------------------------


def test_F16_3_with_no_setup_command_failing_first_runs_as_in_F15(tmp_path: Path) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    commit(
        repo,
        {
            "tests/unit/passes.test.js": JS_PASSING_TEST,
            "tests/unit/calc.test.js": JS_FAILING_TEST,
        },
        "F99 Tests",
        test=True,
    )
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")

    # F15: the passing file is named, the failing one is not.
    expected = ["tests/unit/passes.test.js"]
    for none in (None, ""):
        assert (
            failing_first.check(
                repo,
                base,
                head,
                JS_TEST_COMMAND,
                test_file_pattern=JS_PATTERN,
                setup_command=none,
            )
            == expected
        )
    assert failing_first.check(repo, base, head, JS_TEST_COMMAND, test_file_pattern=JS_PATTERN) == (
        expected
    )

    for extra in (("--setup-command", ""), ()):
        code, out = _cli(repo, base, head, JS_TEST_COMMAND_LINE, *extra)
        assert code != 0, out
        assert "tests/unit/passes.test.js" in out
        assert "tests/unit/calc.test.js" not in out
        assert "error of the run" not in out


# --- F16-4 -----------------------------------------------------------------------------------

MARKER = ".f16-setup-marker"


def _logging_setup(log: Path) -> str:
    """A setup command that logs `<HEAD sha>|<cwd>|<fresh or stale>` and then writes a marker.

    `stale` means the marker of an earlier setup run was visible in this worktree.
    """
    quoted = shlex.quote(str(log))
    return (
        f"if [ -e {MARKER} ]; then state=stale; else state=fresh; fi"
        f' && echo "$(git rev-parse HEAD)|$PWD|$state" >> {quoted}'
        f" && touch {MARKER}"
    )


def test_F16_4_the_setup_command_runs_once_in_each_test_commits_own_worktree(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    first = commit(repo, {"tests/unit/a.test.js": JS_FAILING_TEST}, "F99 Tests 1", test=True)
    second = commit(repo, {"tests/unit/b.test.js": JS_FAILING_TEST}, "F99 Tests 2", test=True)
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")

    for name, run in (
        (
            "check",
            lambda log: failing_first.check(
                repo,
                base,
                head,
                JS_TEST_COMMAND,
                test_file_pattern=JS_PATTERN,
                setup_command=_logging_setup(log),
            ),
        ),
        (
            "cli",
            lambda log: _cli(
                repo, base, head, JS_TEST_COMMAND_LINE, "--setup-command", _logging_setup(log)
            ),
        ),
    ):
        log = tmp_path / f"setup-{name}.log"
        result = run(log)
        if name == "check":
            assert result == []
        else:
            code, out = result
            assert code == 0, out

        assert log.exists(), f"{name}: the setup command never ran"
        lines = [line.split("|") for line in log.read_text(encoding="utf-8").splitlines()]
        # Once per test commit, at that commit.
        assert sorted(sha for sha, _, _ in lines) == sorted([first, second]), lines
        # In two distinct worktrees, neither of them the caller's repository.
        places = {Path(cwd).resolve() for _, cwd, _ in lines}
        assert len(places) == 2, lines
        assert repo.resolve() not in places, lines
        # The marker the setup wrote at the first commit is absent at the second.
        assert [state for _, _, state in lines] == ["fresh", "fresh"], lines
        assert not (repo / MARKER).exists()


# --- Review round: timeouts, setup output, tests not run, one setup per commit, clean-up ------

# A test command kept outside the repository under check: it logs `<HEAD sha>|<file>|<marker>`
# (whether the F16-4 setup marker is in its directory) to the log it is given, and fails.
LOGGING_RUNNER = """import os
import subprocess
import sys

log, path = sys.argv[1], sys.argv[2]
head = subprocess.run(
    ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=False
).stdout.strip()
marker = "yes" if os.path.exists(".f16-setup-marker") else "no"
with open(log, "a", encoding="utf-8") as handle:
    handle.write(f"{head}|{path}|{marker}\\n")
sys.exit(1)
"""


def _logging_runner(tmp_path: Path, log: Path) -> tuple[tuple[str, ...], str]:
    script = tmp_path / "runners" / "logging_runner.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(LOGGING_RUNNER, encoding="utf-8")
    command = (sys.executable, str(script), str(log))
    return command, shlex.join(command)


def _worktrees(repo: Path) -> list[str]:
    listing = git(repo, "worktree", "list", "--porcelain")
    return [line for line in listing.splitlines() if line.startswith("worktree ")]


def _assert_no_worktree_left(repo: Path) -> None:
    trees = _worktrees(repo)
    assert len(trees) == 1, trees
    assert Path(trees[0].removeprefix("worktree ")).resolve() == repo.resolve(), trees


def test_F16_2_a_setup_command_that_times_out_is_an_error_of_the_run_and_its_tests_are_not_run(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    tests = commit(repo, {"tests/unit/calc.test.js": JS_FAILING_TEST}, "F99 Tests", test=True)
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")
    log = tmp_path / "tests-run.log"
    command, command_line = _logging_runner(tmp_path, log)

    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(
            repo,
            base,
            head,
            command,
            timeout=2,
            test_file_pattern=JS_PATTERN,
            setup_command="sleep 30",
        )
    message = str(raised.value)
    assert tests in message
    assert "timed out" in message
    # The commit's added tests are not run after its setup broke.
    assert not log.exists(), log.read_text(encoding="utf-8")
    _assert_no_worktree_left(repo)

    code, out = _cli(
        repo, base, head, command_line, "--timeout", "2", "--setup-command", "sleep 30"
    )
    assert code != 0, out
    assert tests in out
    assert "timed out" in out
    assert "error of the run" in out
    assert not log.exists(), log.read_text(encoding="utf-8")
    _assert_no_worktree_left(repo)


NOISY_BROKEN_SETUP = "echo F16-SETUP-OUT; echo F16-SETUP-ERR >&2; exit 3"


def test_F16_2_a_failed_setup_shows_its_output_and_its_commits_tests_are_not_run(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    tests = commit(repo, {"tests/unit/calc.test.js": JS_FAILING_TEST}, "F99 Tests", test=True)
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")
    log = tmp_path / "tests-run.log"
    command, command_line = _logging_runner(tmp_path, log)

    with pytest.raises(failing_first.RunError) as raised:
        failing_first.check(
            repo,
            base,
            head,
            command,
            test_file_pattern=JS_PATTERN,
            setup_command=NOISY_BROKEN_SETUP,
        )
    message = str(raised.value)
    assert tests in message
    # The setup's stdout and stderr are both in the message.
    assert "F16-SETUP-OUT" in message
    assert "F16-SETUP-ERR" in message
    assert not log.exists(), log.read_text(encoding="utf-8")
    _assert_no_worktree_left(repo)

    code, out = _cli(repo, base, head, command_line, "--setup-command", NOISY_BROKEN_SETUP)
    assert code != 0, out
    assert tests in out
    assert "F16-SETUP-OUT" in out
    assert "F16-SETUP-ERR" in out
    assert "error of the run" in out
    assert not log.exists(), log.read_text(encoding="utf-8")
    _assert_no_worktree_left(repo)


def test_F16_4_the_setup_command_runs_once_for_a_test_commit_that_adds_several_tests(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = _js_repo(tmp_path)
    tests = commit(
        repo,
        {"tests/unit/a.test.js": JS_FAILING_TEST, "tests/unit/b.test.js": JS_FAILING_TEST},
        "F99 Tests",
        test=True,
    )
    head = commit(repo, {"calc.js": JS_CODE}, "F99 Code")

    for name in ("check", "cli"):
        setup_log = tmp_path / f"setup-{name}.log"
        run_log = tmp_path / f"tests-{name}.log"
        command, command_line = _logging_runner(tmp_path, run_log)
        if name == "check":
            assert (
                failing_first.check(
                    repo,
                    base,
                    head,
                    command,
                    test_file_pattern=JS_PATTERN,
                    setup_command=_logging_setup(setup_log),
                )
                == []
            )
        else:
            code, out = _cli(
                repo, base, head, command_line, "--setup-command", _logging_setup(setup_log)
            )
            assert code == 0, out

        assert setup_log.exists(), f"{name}: the setup command never ran"
        setups = setup_log.read_text(encoding="utf-8").splitlines()
        # Exactly one setup run for the one test commit, though it adds two test files.
        assert [line.split("|")[0] for line in setups] == [tests], setups
        # Both files ran at that commit, after the setup (its marker was there).
        runs = sorted(run_log.read_text(encoding="utf-8").splitlines())
        assert runs == [
            f"{tests}|tests/unit/a.test.js|yes",
            f"{tests}|tests/unit/b.test.js|yes",
        ], runs
        # A normal run leaves no worktree behind.
        _assert_no_worktree_left(repo)
