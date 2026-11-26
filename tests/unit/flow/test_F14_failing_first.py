"""F14 Flow checks: failing first, cases F14-1, F14-2 and F14-5 (epic-11 spec, section 2.12).

Each test builds a throwaway repository: a base commit, a test commit (trailer
`Agent: test`) that adds tests, and a later code commit that makes them pass at the
head. The check must judge the tests at the test commit, not at the head.
The interface is in tests/INTERFACE.md, section "F14".
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.flow.repo import (
    TEST_COMMAND,
    cli_test_command,
    commit,
    flowcheck,
    init_repo,
    output,
    run_cli,
)

# Fails at the test commit: `add` still raises NotImplementedError.
FAILING_TEST = """from calc import add


def test_adds_two_numbers():
    assert add(2, 3) == 5
"""

# Errors at the test commit: `multiply` does not exist yet.
ERRORING_TEST = """from calc import multiply


def test_multiplies_two_numbers():
    assert multiply(2, 3) == 6
"""

# Passes at the test commit: it needs nothing the code commit adds.
PASSING_TEST = """def test_already_passes():
    assert "a" + "b" == "ab"
"""

CODE = "def add(a, b):\n    return a + b\n\n\ndef multiply(a, b):\n    return a * b\n"


def _repo_with_tests(tmp_path: Path, tests: dict[str, str]) -> tuple[Path, str, str]:
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, tests, "F99 Tests", test=True)
    head = commit(repo, {"calc.py": CODE}, "F99 Code")
    return repo, base, head


def test_F14_1_every_new_test_fails_at_the_test_commit_so_failing_first_passes(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _repo_with_tests(
        tmp_path,
        {"tests/test_calc.py": FAILING_TEST, "tests/test_multiply.py": ERRORING_TEST},
    )

    assert failing_first.check(repo, base, head, TEST_COMMAND) == []

    result = run_cli(
        "failing_first",
        "--repo",
        str(repo),
        "--base",
        base,
        "--head",
        head,
        "--test-command",
        cli_test_command(),
    )
    assert result.returncode == 0, output(result)


def test_F14_2_a_new_test_that_passes_at_the_test_commit_fails_failing_first_and_is_named(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base, head = _repo_with_tests(
        tmp_path,
        {"tests/test_calc.py": FAILING_TEST, "tests/test_trivial.py": PASSING_TEST},
    )

    passing = failing_first.check(repo, base, head, TEST_COMMAND)
    assert "tests/test_trivial.py::test_already_passes" in passing

    result = run_cli(
        "failing_first",
        "--repo",
        str(repo),
        "--base",
        base,
        "--head",
        head,
        "--test-command",
        cli_test_command(),
    )
    assert result.returncode != 0, output(result)
    assert "tests/test_trivial.py::test_already_passes" in output(result)


def test_F14_5_a_pull_request_with_no_test_commit_that_changes_only_documents_passes_failing_first(
    tmp_path: Path,
) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = init_repo(tmp_path / "repo")
    head = commit(
        repo,
        {"README.md": "# Probe repository\n\nMore words.\n", "docs/guide.md": "# Guide\n"},
        "F99 Documents",
    )

    assert failing_first.check(repo, base, head, TEST_COMMAND) == []

    result = run_cli(
        "failing_first",
        "--repo",
        str(repo),
        "--base",
        base,
        "--head",
        head,
        "--test-command",
        cli_test_command(),
    )
    assert result.returncode == 0, output(result)
