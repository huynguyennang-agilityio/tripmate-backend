"""F14 Flow checks: review round 1 fixes (code agent's own tests, own file).

Failing first per test commit, exit codes, test lock context, merges, support files, test commits
outside tests/, the pragma rule of mutation, and the real mutmut runner with a fake mutmut.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from tests.unit.flow.repo import (
    TEST_COMMAND,
    commit,
    flowcheck,
    git,
    init_repo,
    output,
    run_cli,
)

CODE = "def add(a, b):\n    return a + b\n"
OLD_TEST = "from calc import add\n\n\ndef test_adds():\n    assert add(2, 3) == 5\n"
NEW_TEST = "\n\ndef test_adds_zero():\n    assert add(0, 4) == 4\n"


# ---- R1-5: failing first checks out each test commit ----------------------------------------


def test_failing_first_a_correction_commit_that_adds_a_test_checks_only_what_it_adds(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, {"tests/test_calc.py": OLD_TEST}, "F99 Tests", test=True)
    commit(repo, {"calc.py": CODE}, "F99 Code")
    # The correction after the code: changes the old test and adds one that fails (it needs a
    # function the code lacks). The old test passes now, and must not be asked to fail again.
    corrected = (
        OLD_TEST.replace("== 5", "== 2 + 3")
        + "\n\ndef test_multiplies():\n    from calc import multiply\n\n    assert multiply(2, 3) == 6\n"
    )
    head = commit(repo, {"tests/test_calc.py": corrected}, "F99 Correct and add", test=True)

    assert failing_first.check(repo, base, head, TEST_COMMAND) == []
    result = run_cli(
        "failing_first", "--repo", str(repo), "--base", base, "--head", head,
        "--test-command", " ".join(TEST_COMMAND),
    )  # fmt: skip
    assert result.returncode == 0, output(result)


def test_failing_first_a_correction_that_only_changes_tests_is_not_asked_to_fail(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, {"tests/test_calc.py": OLD_TEST}, "F99 Tests", test=True)
    commit(repo, {"calc.py": CODE}, "F99 Code")
    head = commit(
        repo, {"tests/test_calc.py": OLD_TEST.replace("== 5", "== 2 + 3")}, "F99 Fix", test=True
    )

    assert failing_first.check(repo, base, head, TEST_COMMAND) == []


def test_failing_first_each_test_commit_is_run_at_its_own_commit(tmp_path: Path) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, {"tests/test_calc.py": OLD_TEST}, "F99 Tests", test=True)
    commit(repo, {"calc.py": CODE}, "F99 Code")
    # A second test commit adds a test that already passes (the code exists): named.
    head = commit(repo, {"tests/test_calc.py": OLD_TEST + NEW_TEST}, "F99 More tests", test=True)

    assert failing_first.check(repo, base, head, TEST_COMMAND) == [
        "tests/test_calc.py::test_adds_zero"
    ]


# ---- R1-6: only pytest exit 1 and 2 count as failed first -----------------------------------


@pytest.mark.parametrize(
    "files",
    [
        # exit 4: a conftest that cannot be imported (usage error)
        {"tests/conftest.py": "import not_there\n", "tests/test_calc.py": OLD_TEST},
        # exit 5: the test ID names a function pytest does not collect
        {"tests/test_calc.py": "def test_hidden():\n    assert False\n\ntest_hidden = None\n"},
    ],
)
def test_failing_first_an_exit_other_than_1_or_2_is_an_error_that_fails(
    tmp_path: Path, files: dict[str, str]
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = init_repo(tmp_path / "repo")
    head = commit(repo, {**files}, "F99 Tests", test=True)  # type: ignore[arg-type]

    with pytest.raises(failing_first.RunError):
        failing_first.check(repo, base, head, TEST_COMMAND)
    result = run_cli(
        "failing_first", "--repo", str(repo), "--base", base, "--head", head,
        "--test-command", " ".join(TEST_COMMAND),
    )  # fmt: skip
    assert result.returncode != 0, output(result)
    assert "exit" in output(result)


# ---- R1-3, R1-4, R1-7, R1-12: test lock -----------------------------------------------------

LOCKED = "tests/test_calc.py"
LOCKED_TEST = f"{LOCKED}::test_adds"
CONTEXT_TEST = (
    "from calc import add\n\nEXPECTED = 5\n\n\ndef test_adds():\n    assert add(2, 3) == EXPECTED\n"
)


def _locked(tmp_path: Path, files: dict[str, str] | None = None) -> tuple[Path, str]:
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, files or {LOCKED: CONTEXT_TEST}, "F99 Tests", test=True)
    commit(repo, {"calc.py": CODE}, "F99 Code")
    return repo, base


@pytest.mark.parametrize(
    "edit",
    [
        ("EXPECTED = 5", "EXPECTED = 99"),
        ("from calc import add", "from calc import add as _a\nfrom calc import add"),
        ("EXPECTED = 5", "EXPECTED = 5\n\n\ndef helper():\n    return 1"),
    ],
)
def test_lock_a_change_to_the_module_context_of_a_locked_test_breaks_it(
    tmp_path: Path, edit: tuple[str, str]
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _locked(tmp_path)
    head = commit(repo, {LOCKED: CONTEXT_TEST.replace(*edit)}, "F99 Edit the context")

    assert test_lock.check(repo, base, head) == [LOCKED_TEST]


def test_lock_a_change_that_arrives_through_a_merge_commit_breaks_the_test(
    tmp_path: Path,
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _locked(tmp_path)
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, {LOCKED: CONTEXT_TEST.replace("== EXPECTED", "is not None")}, "F99 Side edit")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"calc.py": CODE + "\nX = 1\n"}, "F99 Main work")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    head = git(repo, "rev-parse", "HEAD")

    assert test_lock.check(repo, base, head) == [LOCKED_TEST]


def test_lock_a_conftest_or_helper_a_test_commit_added_is_locked(tmp_path: Path) -> None:
    test_lock = flowcheck("test_lock")
    helper = "tests/helpers.py"
    repo, base = _locked(
        tmp_path,
        {LOCKED: OLD_TEST, "tests/conftest.py": "X = 1\n", helper: "def h():\n    return 1\n"},
    )
    commit(repo, {"tests/conftest.py": "X = 2\n"}, "F99 Skip things in a hook")
    head = commit(repo, {helper: None}, "F99 Delete the helper")  # type: ignore[dict-item]

    assert test_lock.check(repo, base, head) == ["tests/conftest.py", helper]
    result = run_cli("test_lock", "--repo", str(repo), "--base", base, "--head", head)
    assert result.returncode != 0 and "tests/conftest.py" in output(result)


def test_lock_a_support_file_a_test_commit_changes_again_stays_open_to_test_commits(
    tmp_path: Path,
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _locked(tmp_path, {LOCKED: OLD_TEST, "tests/conftest.py": "X = 1\n"})
    commit(repo, {"tests/conftest.py": "X = 2\n"}, "F99 Fix", test=True)
    commit(repo, {"calc.py": CODE + "\nY = 1\n"}, "F99 Code again")
    head = commit(repo, {"tests/new_helper.py": "Z = 1\n"}, "F99 A new helper of my own")

    assert test_lock.check(repo, base, head) == []


def test_lock_a_test_commit_that_changes_a_path_outside_tests_fails_and_names_the_commit(
    tmp_path: Path,
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = init_repo(tmp_path / "repo")
    sha = commit(repo, {LOCKED: OLD_TEST, "calc.py": CODE}, "F99 Forged trailer", test=True)

    shown = test_lock.check(repo, base, sha)

    assert len(shown) == 1 and sha[:12] in shown[0] and "calc.py" in shown[0]


# ---- R1-8, R1-10, R1-11: mutation ------------------------------------------------------------

RULE = "app/money/splits.py"
RULES = '[[rule]]\nglob = "app/money/splits.py"\nlevel = "Medium"\n'


def _rules(tmp_path: Path) -> object:
    path = tmp_path / "paths.toml"
    path.write_text(RULES, encoding="utf-8")
    return flowcheck("risk_gate").load_rules(path)


@pytest.mark.parametrize(
    "pragma", ["# pragma: no mutate", "#pragma:no cover", "# pragma: no mutate"]
)
def test_mutation_an_added_pragma_fails_unless_listed(tmp_path: Path, pragma: str) -> None:
    mutation = flowcheck("mutation")
    rules = _rules(tmp_path)
    added = lambda path: [f"    return 1  {pragma}"]  # noqa: E731

    shown = mutation.check([RULE], rules, "", lambda files: [], added)
    assert shown == [f"{RULE}#pragma"]

    listed = f"## Surviving mutants\n\n- {RULE}#pragma: the debug branch is not reachable\n"
    assert mutation.check([RULE], rules, listed, lambda files: [], added) == []
    assert mutation.check([RULE], rules, "", lambda files: [], lambda path: ["x = 1"]) == []


def test_mutation_the_command_line_sees_a_pragma_in_the_diff(tmp_path: Path) -> None:
    repo, base = init_repo(tmp_path / "repo", {"README.md": "# x\n"})
    head = commit(repo, {RULE: "def f():\n    return 1  # pragma: no mutate\n"}, "F99 Rule")
    from flowchecks import mutation

    added = mutation.added_lines_of(str(repo), base, head)
    assert any("pragma: no mutate" in line for line in added(RULE))


RUN_OK = "Generating mutants\n    done in 31ms (1 files mutated, 0 ignored, 0 unmodified)\n"


def _fake_mutmut(
    results: str, *, run_code: int = 0, run_out: str = RUN_OK, run_err: str = ""
) -> tuple[object, list[tuple[str, ...]]]:
    calls: list[tuple[str, ...]] = []

    def fake(repo: object, *args: str) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[0] == "run":
            return subprocess.CompletedProcess(args, run_code, run_out, run_err)
        return subprocess.CompletedProcess(args, 0, results, "")

    return fake, calls


REALISTIC_RESULTS = """    app.money.splits.x_allocate__mutmut_3: survived
    app.money.splits.x_allocate__mutmut_4: no tests
    app.money.splits.x_allocate__mutmut_5: not checked
    app.money.splits.x_allocate__mutmut_6: check was interrupted by user
    app.money.splits.x_allocate__mutmut_7: timeout
    app.money.splits.x_allocate__mutmut_8: caught by type check
    app.money.splits.x_allocate__mutmut_9: segfault
    app.money.balances.x_compute__mutmut_1: survived
"""


def test_mutmut_runner_returns_survivors_untested_and_unchecked_mutants_of_the_given_files(
    tmp_path: Path,
) -> None:
    mutation = flowcheck("mutation")
    fake, calls = _fake_mutmut(REALISTIC_RESULTS)

    survivors = mutation.mutmut_runner(tmp_path, fake)([RULE])

    assert calls == [("run", "app.money.splits.*"), ("results",)]
    assert sorted(survivors) == [f"app.money.splits.x_allocate__mutmut_{n}" for n in (3, 4, 5, 6)]


def test_mutmut_runner_fails_when_nothing_matches_but_the_file_has_a_function(
    tmp_path: Path,
) -> None:
    mutation = flowcheck("mutation")
    (tmp_path / "app" / "money").mkdir(parents=True)
    (tmp_path / RULE).write_text("def allocate():\n    return 1\n", encoding="utf-8")
    drift = "AssertionError: Filtered for specific mutants, but nothing matches\n"
    fake, _ = _fake_mutmut("", run_code=1, run_err=drift)

    with pytest.raises(RuntimeError, match="mutmut run failed"):
        mutation.mutmut_runner(tmp_path, fake)([RULE])


def test_mutmut_runner_passes_a_file_with_no_function_when_nothing_matches(
    tmp_path: Path,
) -> None:
    mutation = flowcheck("mutation")
    (tmp_path / "app" / "money").mkdir(parents=True)
    (tmp_path / RULE).write_text("LIMIT = 3\n", encoding="utf-8")
    drift = "AssertionError: Filtered for specific mutants, but nothing matches\n"
    fake, _ = _fake_mutmut("", run_code=1, run_err=drift)

    assert mutation.mutmut_runner(tmp_path, fake)([RULE]) == []


def test_mutmut_runner_fails_when_mutmut_itself_fails(tmp_path: Path) -> None:
    mutation = flowcheck("mutation")
    fake, _ = _fake_mutmut("", run_code=1, run_err="Failed to run clean test\n")

    with pytest.raises(RuntimeError):
        mutation.mutmut_runner(tmp_path, fake)([RULE])


# ---- R1-9, R1-13, R1-15, R1-16: the workflows ------------------------------------------------

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def _text(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_a_push_removes_the_approval_in_a_job_of_its_own_that_runs_nothing_of_the_head() -> None:
    reusable = _text("risk-gate-check.yml")
    revoke = reusable[reusable.index("  revoke-approval:") : reusable.index("  risk-gate:")]

    assert "github.event.action == 'synchronize'" in revoke
    assert "pull-requests: write" in revoke
    assert "checkout" not in revoke and "uses:" not in revoke
    assert len(re.findall(r"^\s+pull-requests: write$", reusable, re.MULTILINE)) == 1
    assert "gh api --method DELETE" in revoke and "human-approved" in revoke
    gate = reusable[reusable.index("  risk-gate:") :]
    assert "needs: revoke-approval" in gate
    assert "persist-credentials: true" not in reusable
    assert len(re.findall(r"^\s+pull-requests: write$", _text("risk-gate.yml"), re.MULTILINE)) == 1


def test_the_other_repositories_get_the_safe_default_and_labels_do_not_restart_the_checks() -> None:
    checks = _text("flow-checks.yml")
    after = checks[checks.index("      run-risk-gate:") :]

    assert "default: false" in after[: after.index("flowchecks-ref:")]
    assert "types: [opened, synchronize, reopened, edited]" in _text("flow.yml")
