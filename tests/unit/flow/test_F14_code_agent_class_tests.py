"""F14 Flow checks: tests inside `Test*` classes (code agent's own tests, own file).

A method is identified as `<path>::TestX::test_y`, pytest's node ID.
"""

from __future__ import annotations

from pathlib import Path

from tests.unit.flow.repo import (
    TEST_COMMAND,
    commit,
    flowcheck,
    init_repo,
    output,
    run_cli,
)

FILE = "tests/test_calc.py"
LOCKED = f"{FILE}::TestAdd::test_adds_two_numbers"

CLASS_TESTS = """from calc import add


class TestAdd:
    def test_adds_two_numbers(self):
        assert add(2, 3) == 5

    def helper(self):
        return 1
"""
CODE = "def add(a, b):\n    return a + b\n"
PASSING = """class TestTrivial:
    def test_already_passes(self):
        assert "a" + "b" == "ab"
"""


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, {FILE: CLASS_TESTS}, "F99 Tests", test=True)
    commit(repo, {"calc.py": CODE}, "F99 Code")
    return repo, base


def test_lock_names_a_method_that_a_code_commit_edits_skips_or_deletes(tmp_path: Path) -> None:
    test_lock = flowcheck("test_lock")
    variants = {
        "edit": CLASS_TESTS.replace("== 5", "is not None"),
        "method skip": CLASS_TESTS.replace(
            "    def test_adds", '    @pytest.mark.skip(reason="x")\n    def test_adds'
        ),
        "class skip": CLASS_TESTS.replace(
            "class TestAdd:", '@pytest.mark.skip(reason="x")\nclass TestAdd:'
        ),
        "class attribute skip": CLASS_TESTS.replace(
            "class TestAdd:\n", "class TestAdd:\n    pytestmark = pytest.mark.skip\n\n"
        ),
        "delete method": "class TestAdd:\n    def helper(self):\n        return 1\n",
        "delete class": "def test_other():\n    pass\n",
    }
    for name, source in variants.items():
        repo, base = _repo(tmp_path / name.replace(" ", "_"))
        head = commit(repo, {FILE: source}, "F99 Weaken")
        assert test_lock.check(repo, base, head) == [LOCKED], name
        result = run_cli("test_lock", "--repo", str(repo), "--base", base, "--head", head)
        assert result.returncode != 0 and LOCKED in output(result), name


def test_lock_lets_a_code_commit_add_a_method_and_change_a_helper(tmp_path: Path) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _repo(tmp_path)
    source = CLASS_TESTS.replace("return 1", "return 2") + (
        "\n    def test_own(self):\n        assert add(1, 1) == 2\n"
    )
    head = commit(repo, {FILE: source}, "F99 Own method")

    assert test_lock.check(repo, base, head) == []


def test_failing_first_runs_class_methods_and_names_one_that_passes(tmp_path: Path) -> None:
    failing_first = flowcheck("failing_first")
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, {FILE: CLASS_TESTS, "tests/test_trivial.py": PASSING}, "F99 Tests", test=True)
    head = commit(repo, {"calc.py": CODE}, "F99 Code")

    passing = failing_first.check(repo, base, head, TEST_COMMAND)

    assert passing == ["tests/test_trivial.py::TestTrivial::test_already_passes"]
