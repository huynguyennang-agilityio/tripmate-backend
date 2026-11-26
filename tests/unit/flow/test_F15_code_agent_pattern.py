"""F15 code agent: the test file pattern matching of flowchecks.failing_first."""

from __future__ import annotations

import pytest

from tests.unit.flow.repo import flowcheck


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
