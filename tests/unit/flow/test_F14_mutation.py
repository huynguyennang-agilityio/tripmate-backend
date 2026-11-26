"""F14 Flow checks: mutation, case F14-11 (epic-11 spec, section 2.12; task scope, version 1.2).

Mutation testing itself (mutmut) is behind the `run_mutants` argument of the check, so
these tests give it a stand-in that returns the surviving mutants. A surviving mutant
counts as listed only when the pull request description has a `## Surviving mutants`
section with one line that names it and gives a reason.
The interface is in tests/INTERFACE.md, section "F14".
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

import pytest

from tests.support import REPO_ROOT
from tests.unit.flow.repo import flowcheck

BACKEND_PATH_LIST = REPO_ROOT / ".github" / "risk-paths.toml"

OWN_PATH_LIST = """
[[rule]]
glob = "app/money/splits.py"
level = "Medium"

[[rule]]
glob = "README.md"
level = "Low"
"""

RULE_FILE = "app/money/splits.py"
CHANGED = (RULE_FILE, "README.md")
SURVIVOR = "app.money.splits.x_allocate__mutmut_3"
OTHER = "app.money.splits.x_allocate__mutmut_7"

# Descriptions that do not list SURVIVOR.
UNLISTED = {
    "no_section": "## Summary\n\nSplits.\n",
    "section_lists_another_mutant": (
        "## Summary\n\nSplits.\n\n## Surviving mutants\n\n"
        f"- {OTHER}: equivalent, the bound is never reached\n"
    ),
    "line_names_it_with_no_reason": (
        f"## Summary\n\nSplits.\n\n## Surviving mutants\n\n- {SURVIVOR}\n"
    ),
    "named_under_another_heading": (
        "## Summary\n\nSplits.\n\n## Surviving mutants\n\n"
        f"- {OTHER}: equivalent, the bound is never reached\n\n"
        f"## Notes\n\n- {SURVIVOR}: equivalent, the bound is never reached\n"
    ),
}

LISTED = (
    "## Summary\n\nSplits.\n\n## Surviving mutants\n\n"
    f"- {SURVIVOR}: equivalent, the remainder is always zero here\n"
)


def _runner(
    survivors: Iterable[str],
) -> tuple[Callable[[Sequence[str]], list[str]], list[list[str]]]:
    calls: list[list[str]] = []

    def run_mutants(rule_files: Sequence[str]) -> list[str]:
        calls.append(sorted(rule_files))
        return list(survivors)

    return run_mutants, calls


def _own_rules(tmp_path: Path) -> object:
    path = tmp_path / "risk-paths.toml"
    path.write_text(OWN_PATH_LIST, encoding="utf-8")
    return flowcheck("risk_gate").load_rules(path)


@pytest.mark.parametrize("description", sorted(UNLISTED))
def test_F14_11_a_changed_rule_file_with_a_surviving_mutant_the_pull_request_does_not_list_fails_and_shows_it(
    tmp_path: Path, description: str
) -> None:
    mutation = flowcheck("mutation")
    run_mutants, calls = _runner([SURVIVOR])

    shown = mutation.check(CHANGED, _own_rules(tmp_path), UNLISTED[description], run_mutants)

    assert SURVIVOR in shown
    assert calls == [[RULE_FILE]]


def test_F14_11_a_surviving_mutant_listed_with_a_reason_does_not_fail_the_check(
    tmp_path: Path,
) -> None:
    mutation = flowcheck("mutation")
    run_mutants, calls = _runner([SURVIVOR])

    assert mutation.check(CHANGED, _own_rules(tmp_path), LISTED, run_mutants) == []
    assert calls == [[RULE_FILE]]


def test_F14_11_the_backend_path_list_makes_a_changed_money_split_file_a_rule_file() -> None:
    mutation = flowcheck("mutation")
    assert BACKEND_PATH_LIST.is_file(), f"{BACKEND_PATH_LIST} does not exist"
    rules = flowcheck("risk_gate").load_rules(BACKEND_PATH_LIST)
    run_mutants, calls = _runner([SURVIVOR])

    shown = mutation.check(CHANGED, rules, UNLISTED["no_section"], run_mutants)

    assert SURVIVOR in shown
    assert calls == [[RULE_FILE]]
