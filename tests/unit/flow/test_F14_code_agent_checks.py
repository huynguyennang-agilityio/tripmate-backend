"""F14 Flow checks: tests the code agent added for choices the cases leave open.

They sit in their own file; the test agent's tests are unchanged. The interface is in
tests/INTERFACE.md, section "F14".
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from tests.support import REPO_ROOT
from tests.unit.flow.repo import commit, flowcheck, init_repo, output, run_cli

LOCKED_FILE = "tests/test_calc.py"
LOCKED_TEST = f"{LOCKED_FILE}::test_adds_two_numbers"
TESTS = """from calc import add


def test_adds_two_numbers():
    assert add(2, 3) == 5
"""
CODE = "def add(a, b):\n    return a + b\n"


def _locked_repo(tmp_path: Path) -> tuple[Path, str]:
    repo, base = init_repo(tmp_path / "repo")
    commit(repo, {LOCKED_FILE: TESTS}, "F99 Tests", test=True)
    commit(repo, {"calc.py": CODE}, "F99 Code")
    return repo, base


def test_test_lock_a_new_test_added_to_a_locked_file_by_a_code_commit_does_not_fail(
    tmp_path: Path,
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _locked_repo(tmp_path)
    head = commit(
        repo,
        {
            LOCKED_FILE: TESTS
            + "\n\ndef test_the_code_agents_own_test():\n    assert add(1, 1) == 2\n"
        },
        "F99 A test of my own",
    )

    assert test_lock.check(repo, base, head) == []
    result = run_cli("test_lock", "--repo", str(repo), "--base", base, "--head", head)
    assert result.returncode == 0, output(result)


def test_test_lock_a_comment_or_blank_line_in_a_locked_file_does_not_fail(tmp_path: Path) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _locked_repo(tmp_path)
    head = commit(
        repo,
        {LOCKED_FILE: "# a comment\n" + TESTS.replace("\n\n\ndef", "\n\n\n\ndef")},
        "F99 Tidy",
    )

    assert test_lock.check(repo, base, head) == []


def test_test_lock_a_new_test_added_by_a_code_commit_is_not_locked_so_it_may_change_later(
    tmp_path: Path,
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _locked_repo(tmp_path)
    own = "\n\ndef test_own():\n    assert add(1, 1) == 2\n"
    commit(repo, {LOCKED_FILE: TESTS + own}, "F99 Own test")
    head = commit(repo, {LOCKED_FILE: TESTS + own.replace("== 2", "== 1 + 1")}, "F99 Edit own test")

    assert test_lock.check(repo, base, head) == []


def test_test_lock_a_code_commit_that_adds_a_skip_to_the_file_breaks_every_locked_test_in_it(
    tmp_path: Path,
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = _locked_repo(tmp_path)
    skipped = TESTS.replace(
        "from calc import add",
        'import pytest\nfrom calc import add\n\npytestmark = [pytest.mark.skipif(True, reason="x")]',
    )
    head = commit(repo, {LOCKED_FILE: skipped}, "F99 Skip")

    assert test_lock.check(repo, base, head) == [LOCKED_TEST]


def test_a_test_commit_needs_the_trailer_in_the_last_paragraph_of_its_message(
    tmp_path: Path,
) -> None:
    git_helpers = flowcheck("_git")
    repo, base = init_repo(tmp_path / "repo")
    commit(
        repo, {"a.txt": "a"}, "F99 Mentions Agent: test in its body\n\nAgent: test is not a trailer"
    )
    commit(repo, {"b.txt": "b"}, "F99 A real trailer", test=True)

    found = git_helpers.commits(repo, base, "HEAD")

    assert [c.is_test for c in found] == [False, True]


BROAD_LOWER_RULES = """
[[rule]]
glob = "**"
level = "Low"

[[rule]]
glob = ".github/**"
level = "Low"

[[rule]]
glob = ".claude/**"
level = "Low"

[[rule]]
glob = "CLAUDE.md"
level = "Low"

[[rule]]
glob = "**/CLAUDE.md"
level = "Low"
"""


@pytest.mark.parametrize(
    "path",
    [
        ".github/flow/paths.toml",
        ".github/workflows/anything.yml",
        "CLAUDE.md",
        "app/trips/CLAUDE.md",
        ".claude/agents/code-agent.md",
        ".claude/agents/new-role.md",
    ],
)
def test_the_built_in_high_paths_stay_high_under_a_broader_lower_rule(
    tmp_path: Path, path: str
) -> None:
    risk_gate = flowcheck("risk_gate")
    lists = tmp_path / "repo" / ".github" / "flow"
    lists.mkdir(parents=True)
    rules_file = lists / "paths.toml"
    rules_file.write_text(BROAD_LOWER_RULES, encoding="utf-8")

    rules = risk_gate.load_rules(rules_file, tmp_path / "repo")

    assert risk_gate.level_of(path, rules) == "High"
    assert risk_gate.check([path], rules, []) == [path]
    assert risk_gate.check([path], rules, ["human-approved"]) == []
    assert risk_gate.level_of("app/trips/service.py", rules) == "Low"


def test_the_path_list_is_high_when_a_command_line_run_names_it_relative_to_the_repository(
    tmp_path: Path,
) -> None:
    repo, base = init_repo(tmp_path / "repo", {"README.md": "# x\n"})
    (repo / ".github").mkdir()
    (repo / ".github" / "paths.toml").write_text(BROAD_LOWER_RULES, encoding="utf-8")
    head = commit(repo, {".github/paths.toml": BROAD_LOWER_RULES + "\n# edited\n"}, "F99 Edit")

    result = run_cli(
        "risk_gate",
        "--rules",
        ".github/paths.toml",
        "--repo",
        str(repo),
        "--base",
        base,
        "--head",
        head,
    )

    assert result.returncode != 0, output(result)
    assert ".github/paths.toml" in output(result)


def test_a_path_list_with_an_unknown_level_or_no_rule_is_refused(tmp_path: Path) -> None:
    risk_gate = flowcheck("risk_gate")
    bad_level = tmp_path / "bad_level.toml"
    bad_level.write_text('[[rule]]\nglob = "a"\nlevel = "high"\n', encoding="utf-8")
    empty = tmp_path / "empty.toml"
    empty.write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="level"):
        risk_gate.load_rules(bad_level)
    with pytest.raises(ValueError, match="no \\[\\[rule\\]\\]"):
        risk_gate.load_rules(empty)


def test_the_highest_level_wins_and_a_medium_rule_makes_a_rule_file() -> None:
    risk_gate = flowcheck("risk_gate")
    rules = risk_gate.load_rules(REPO_ROOT / ".github" / "risk-paths.toml", REPO_ROOT)

    assert risk_gate.level_of("app/money/splits.py", rules) == "High"
    assert risk_gate.is_rule_file("app/money/splits.py", rules)
    assert risk_gate.is_rule_file("app/trips/lifecycle.py", rules)
    assert risk_gate.level_of("app/trips/lifecycle.py", rules) == "Medium"
    assert not risk_gate.is_rule_file("app/trips/service.py", rules)


def test_the_backend_path_list_makes_the_high_items_of_the_flow_high() -> None:
    risk_gate = flowcheck("risk_gate")
    rules = risk_gate.load_rules(REPO_ROOT / ".github" / "risk-paths.toml", REPO_ROOT)
    high = [
        "migrations/env.py",
        "app/trips/models.py",
        "app/trips/schemas.py",
        "app/trips/api.py",
        "app/trips/events.py",
        "app/platform/models.py",
        "app/platform/db.py",
        "app/platform/rate_limits.py",
        "app/main.py",
        "app/worker.py",
        "app/scheduler.py",
        "tests/requirement-ids.txt",
        "app/identity/service.py",
        "app/platform/access.py",
        "app/platform/config.py",
        "app/assistant/prompts.py",
        "pyproject.toml",
        "uv.lock",
        "Dockerfile",
        "compose.yaml",
        "render.yaml",
        ".github/risk-paths.toml",
        "flowchecks/mutation.py",
        "docs/CLAUDE.md",
        ".claude/agents/code-agent.md",
    ]
    low = ["app/trips/service.py", "app/trips/repository.py", "tests/unit/x.py", "tasks/EPIC-11.md"]

    assert risk_gate.check(high + low, rules, []) == sorted(high)


def test_mutation_runs_nothing_when_no_rule_file_changed() -> None:
    mutation = flowcheck("mutation")
    rules = flowcheck("risk_gate").load_rules(REPO_ROOT / ".github" / "risk-paths.toml", REPO_ROOT)
    calls: list[object] = []

    shown = mutation.check(
        ["app/trips/service.py", "README.md", "app/money/notes.md"],
        rules,
        "",
        lambda files: calls.append(files) or ["never"],
    )

    assert shown == []
    assert calls == []


def test_mutation_lists_a_mutant_with_backticks_and_other_bullets_and_ignores_an_empty_reason() -> (
    None
):
    mutation = flowcheck("mutation")
    description = (
        "## Surviving mutants\n\n"
        "* `a.b.x_f__mutmut_1`: equivalent, never reached\n"
        "- a.b.x_f__mutmut_2:   \n"
        "- a.b.x_f__mutmut_3: bound is a constant\n"
        "### Detail\n"
        "- a.b.x_f__mutmut_4: still in the section\n"
        "## Next\n"
        "- a.b.x_f__mutmut_5: not in the section\n"
    )

    assert mutation.listed_mutants(description) == {
        "a.b.x_f__mutmut_1",
        "a.b.x_f__mutmut_3",
        "a.b.x_f__mutmut_4",
    }


WORKFLOWS = REPO_ROOT / ".github" / "workflows"


def _run_scripts(text: str) -> list[str]:
    """The text of every `run:` step of a workflow file (one-line and block forms)."""
    scripts: list[str] = []
    lines = text.splitlines()
    for number, line in enumerate(lines):
        match = re.match(r"^(\s*)(?:- )?run:\s*(.*)$", line)
        if not match:
            continue
        indent, rest = len(match.group(1)), match.group(2)
        if rest and not rest.startswith(("|", ">")):
            scripts.append(rest)
            continue
        body: list[str] = []
        for following in lines[number + 1 :]:
            if following.strip() and len(following) - len(following.lstrip()) <= indent:
                break
            body.append(following)
        scripts.append("\n".join(body))
    return scripts


@pytest.mark.parametrize(
    "name", ["flow-checks.yml", "flow.yml", "risk-gate-check.yml", "risk-gate.yml"]
)
def test_no_run_script_of_a_flow_workflow_interpolates_an_expression(name: str) -> None:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    scripts = _run_scripts(text)

    assert scripts or name in ("flow.yml", "risk-gate.yml"), "the scan found no run step"
    assert [s for s in scripts if "${{" in s] == []


def test_the_reusable_workflow_has_the_four_jobs_least_privilege_and_pinned_actions() -> None:
    text = (WORKFLOWS / "flow-checks.yml").read_text(encoding="utf-8")

    for job in ("failing-first", "test-lock", "mutation", "risk-gate"):
        assert re.search(rf"^  {job}:\n    name: {job}$", text, re.MULTILINE), job
    assert re.search(r"^permissions:\n  contents: read$", text, re.MULTILINE)
    assert "workflow_call:" in text
    assert text.count("fetch-depth: 0") == 4
    for used in re.findall(r"uses:\s*(\S+)", text):
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", used), used


def test_the_risk_gate_runs_from_the_base_and_never_checks_out_or_runs_the_head() -> None:
    caller = (WORKFLOWS / "risk-gate.yml").read_text(encoding="utf-8")
    reusable = (WORKFLOWS / "risk-gate-check.yml").read_text(encoding="utf-8")

    assert "pull_request_target:" in caller
    assert "uses: ./.github/workflows/risk-gate-check.yml" in caller
    assert "flowchecks-ref: ${{ github.event.pull_request.base.sha }}" in caller
    assert re.search(r"^permissions:\n  contents: read$", reusable, re.MULTILINE)
    for used in re.findall(r"uses:\s*(\S+)", reusable):
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", used), used
    # Every checkout names the base commit or the checks' own repository, never the head.
    refs = re.findall(r"^\s+ref:\s*(.+)$", reusable, re.MULTILINE)
    assert refs == [
        "${{ github.event.pull_request.base.sha }}",
        "${{ inputs.flowchecks-ref }}",
    ]
    assert "git checkout" not in reusable
    assert "git fetch" in reusable


def test_the_backend_runs_its_checks_from_the_base_and_leaves_the_gate_to_the_target_workflow() -> (
    None
):
    caller = (WORKFLOWS / "flow.yml").read_text(encoding="utf-8")

    assert "flowchecks-ref: ${{ github.event.pull_request.base.sha }}" in caller
    assert "run-risk-gate: false" in caller
    assert "pull_request_target" not in caller.replace("pull_request_target`", "")
