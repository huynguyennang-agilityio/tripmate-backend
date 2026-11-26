"""F14 Flow checks: the risk gate, cases F14-6 to F14-10 (epic-11 spec, section 2.12).

F14-6 to F14-8 and F14-10 use a small path list of their own. F14-9 checks the
backend's real path list, `.github/risk-paths.toml`, against development-flow.md,
section 2.1; F14-6 and F14-7 also check it.
The interface is in tests/INTERFACE.md, section "F14".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.support import REPO_ROOT
from tests.unit.flow.repo import commit, flowcheck, init_repo, output, run_cli

LABEL = "human-approved"
BACKEND_PATH_LIST = REPO_ROOT / ".github" / "risk-paths.toml"

OWN_PATH_LIST = """
[[rule]]
glob = "migrations/**"
level = "High"

[[rule]]
glob = "app/platform/**"
level = "High"

[[rule]]
glob = "app/trips/**"
level = "Low"

[[rule]]
glob = "tests/unit/trips/**"
level = "Low"

[[rule]]
glob = "README.md"
level = "Low"
"""

MODULE_DIFF = ("app/trips/service.py", "app/trips/api.py", "tests/unit/trips/test_trips.py")
MIGRATION = "migrations/versions/0002_add_trips.py"
MIGRATION_DIFF = (MIGRATION, "app/trips/service.py")
UNNAMED = "scripts/new_tool.sh"


@pytest.fixture
def own_rules_file(tmp_path: Path) -> Path:
    path = tmp_path / "risk-paths.toml"
    path.write_text(OWN_PATH_LIST, encoding="utf-8")
    return path


def _backend_rules() -> object:
    risk_gate = flowcheck("risk_gate")
    assert BACKEND_PATH_LIST.is_file(), f"{BACKEND_PATH_LIST} does not exist"
    return risk_gate.load_rules(BACKEND_PATH_LIST)


def _repo_with_diff(tmp_path: Path, paths: tuple[str, ...]) -> tuple[Path, str, str]:
    repo, base = init_repo(tmp_path / "repo", {"README.md": "# Probe\n"})
    head = commit(repo, {path: f"# {path}\n" for path in paths}, "F99 Change")
    return repo, base, head


def _cli(rules: Path, repo: Path, base: str, head: str, *labels: str) -> object:
    label_args = [arg for label in labels for arg in ("--label", label)]
    return run_cli(
        "risk_gate",
        "--rules",
        str(rules),
        "--repo",
        str(repo),
        "--base",
        base,
        "--head",
        head,
        *label_args,
    )


def test_F14_6_a_diff_inside_one_module_on_no_listed_path_passes_with_no_label(
    tmp_path: Path, own_rules_file: Path
) -> None:
    risk_gate = flowcheck("risk_gate")
    rules = risk_gate.load_rules(own_rules_file)

    assert risk_gate.check(MODULE_DIFF, rules, []) == []

    repo, base, head = _repo_with_diff(tmp_path, MODULE_DIFF)
    result = _cli(own_rules_file, repo, base, head)
    assert result.returncode == 0, output(result)


def test_F14_6_a_diff_inside_one_module_passes_the_backend_path_list_with_no_label() -> None:
    risk_gate = flowcheck("risk_gate")
    assert risk_gate.check(["app/trips/service.py"], _backend_rules(), []) == []


def test_F14_7_a_diff_that_touches_a_migration_with_no_label_fails_and_names_the_path(
    tmp_path: Path, own_rules_file: Path
) -> None:
    risk_gate = flowcheck("risk_gate")
    rules = risk_gate.load_rules(own_rules_file)

    needing_approval = risk_gate.check(MIGRATION_DIFF, rules, [])
    assert MIGRATION in needing_approval
    assert "app/trips/service.py" not in needing_approval

    repo, base, head = _repo_with_diff(tmp_path, MIGRATION_DIFF)
    result = _cli(own_rules_file, repo, base, head)
    assert result.returncode != 0, output(result)
    assert MIGRATION in output(result)


def test_F14_7_a_migration_with_no_label_fails_the_backend_path_list() -> None:
    risk_gate = flowcheck("risk_gate")
    assert MIGRATION in risk_gate.check([MIGRATION], _backend_rules(), [])


def test_F14_8_the_same_migration_diff_with_the_label_human_approved_passes(
    tmp_path: Path, own_rules_file: Path
) -> None:
    risk_gate = flowcheck("risk_gate")
    rules = risk_gate.load_rules(own_rules_file)

    assert risk_gate.check(MIGRATION_DIFF, rules, [LABEL]) == []

    repo, base, head = _repo_with_diff(tmp_path, MIGRATION_DIFF)
    result = _cli(own_rules_file, repo, base, head, LABEL)
    assert result.returncode == 0, output(result)


# The path list itself, a workflow, every CLAUDE.md, and the agent roles (spec 2.12).
FLOW_PATHS = (
    ".github/risk-paths.toml",
    ".github/workflows/ci.yml",
    ".github/workflows/flow-checks.yml",
    "CLAUDE.md",
    "app/trips/CLAUDE.md",
    ".claude/agents/test-agent.md",
    ".claude/agents/code-agent.md",
)


@pytest.mark.parametrize("path", FLOW_PATHS)
def test_F14_9_a_diff_that_changes_the_path_list_a_workflow_a_claude_md_or_an_agent_role_is_high(
    path: str,
) -> None:
    risk_gate = flowcheck("risk_gate")
    rules = _backend_rules()

    assert risk_gate.level_of(path, rules) == "High"
    assert path in risk_gate.check([path], rules, [])
    assert risk_gate.check([path], rules, [LABEL]) == []


def test_F14_10_a_diff_on_a_path_that_no_rule_names_is_high(
    tmp_path: Path, own_rules_file: Path
) -> None:
    risk_gate = flowcheck("risk_gate")
    rules = risk_gate.load_rules(own_rules_file)

    assert risk_gate.level_of(UNNAMED, rules) == "High"
    assert UNNAMED in risk_gate.check([UNNAMED, "README.md"], rules, [])
    assert risk_gate.check([UNNAMED, "README.md"], rules, [LABEL]) == []

    repo, base, head = _repo_with_diff(tmp_path, (UNNAMED,))
    result = _cli(own_rules_file, repo, base, head)
    assert result.returncode != 0, output(result)
    assert UNNAMED in output(result)
