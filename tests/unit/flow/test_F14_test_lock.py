"""F14 Flow checks: test lock, cases F14-3, F14-4 and F14-5 (epic-11 spec, section 2.12).

The test commit (trailer `Agent: test`) adds `tests/test_calc.py` with two tests.
Later commits then change, skip, or delete one of them, with or without the trailer.
The interface is in tests/INTERFACE.md, section "F14".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.unit.flow.repo import commit, flowcheck, init_repo, output, run_cli

LOCKED_FILE = "tests/test_calc.py"
LOCKED_TEST = f"{LOCKED_FILE}::test_adds_two_numbers"

TESTS = """from calc import add


def test_adds_two_numbers():
    assert add(2, 3) == 5


def test_adds_zero():
    assert add(0, 4) == 4
"""

EDITED = TESTS.replace("assert add(2, 3) == 5", "assert add(2, 3) is not None")

SKIPPED_BY_MARKER = TESTS.replace(
    "def test_adds_two_numbers():",
    '@pytest.mark.skip(reason="later")\ndef test_adds_two_numbers():',
).replace("from calc import add", "import pytest\n\nfrom calc import add")

SKIPPED_BY_MODULE_MARKER = TESTS.replace(
    "from calc import add",
    'import pytest\n\nfrom calc import add\n\npytestmark = pytest.mark.skip(reason="later")',
)

DELETED_FUNCTION = """from calc import add


def test_adds_zero():
    assert add(0, 4) == 4
"""

CODE = "def add(a, b):\n    return a + b\n"

# Each way a later commit can weaken the locked test (F14-3).
WEAKENINGS: dict[str, dict[str, str | None]] = {
    "edits_the_body": {LOCKED_FILE: EDITED},
    "adds_a_skip_marker": {LOCKED_FILE: SKIPPED_BY_MARKER},
    "skips_the_whole_file": {LOCKED_FILE: SKIPPED_BY_MODULE_MARKER},
    "deletes_the_function": {LOCKED_FILE: DELETED_FUNCTION},
    "deletes_the_file": {LOCKED_FILE: None},
}


def _repo_after_test_commit(tmp_path: Path) -> tuple[Path, str]:
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, {LOCKED_FILE: TESTS}, "F99 Tests", test=True)
    commit(repo, {"calc.py": CODE}, "F99 Code")
    return repo, base


@pytest.mark.parametrize("weakening", sorted(WEAKENINGS))
def test_F14_3_a_later_commit_without_the_trailer_that_changes_a_locked_test_fails_and_names_it(
    tmp_path: Path, weakening: str
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _repo_after_test_commit(tmp_path)
    head = commit(repo, WEAKENINGS[weakening], "F99 Make it pass")

    assert LOCKED_TEST in test_lock.check(repo, base, head)

    result = run_cli("test_lock", "--repo", str(repo), "--base", base, "--head", head)
    assert result.returncode != 0, output(result)
    assert LOCKED_TEST in output(result)


def test_F14_4_a_later_commit_with_the_trailer_may_change_a_locked_test(tmp_path: Path) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _repo_after_test_commit(tmp_path)
    commit(repo, {LOCKED_FILE: EDITED}, "F99 Correct the test", test=True)
    head = commit(repo, {"calc.py": CODE + "\n\nVERSION = 2\n"}, "F99 Code again")

    assert test_lock.check(repo, base, head) == []

    result = run_cli("test_lock", "--repo", str(repo), "--base", base, "--head", head)
    assert result.returncode == 0, output(result)


def test_F14_5_a_pull_request_with_no_test_commit_that_changes_only_documents_passes_test_lock(
    tmp_path: Path,
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = init_repo(tmp_path / "repo")
    head = commit(
        repo,
        {"README.md": "# Probe repository\n\nMore words.\n", "docs/guide.md": "# Guide\n"},
        "F99 Documents",
    )

    assert test_lock.check(repo, base, head) == []

    result = run_cli("test_lock", "--repo", str(repo), "--base", base, "--head", head)
    assert result.returncode == 0, output(result)
