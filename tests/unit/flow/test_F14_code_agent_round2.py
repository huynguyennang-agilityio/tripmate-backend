"""F14 Flow checks: review round 2 fixes (code agent's own tests, own file).

R2-1/R2-4: `human-approved` counts only when added after the head commit reached the pull request,
by times GitHub sets.
R2-2: a commit without the trailer that touches any `conftest.py` breaks the test lock.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.unit.flow.repo import commit, flowcheck, init_repo, output, run_cli

LABEL = "human-approved"


def labeled(at: str, name: str = LABEL) -> dict[str, object]:
    return {"event": "labeled", "created_at": at, "label": {"name": name}}


def unlabeled(at: str, name: str = LABEL) -> dict[str, object]:
    return {"event": "unlabeled", "created_at": at, "label": {"name": name}}


def committed(at: str) -> dict[str, object]:
    return {"event": "committed", "committer": {"date": at}, "author": {"date": at}}


def force_pushed(at: str) -> dict[str, object]:
    return {"event": "head_ref_force_pushed", "created_at": at}


def suite(
    at: str, *, app: str = "github-actions", branch: str = "feat/x", prs: tuple[int, ...] = (7,)
) -> dict[str, object]:
    return {
        "created_at": at,
        "head_branch": branch,
        "app": {"slug": app},
        "pull_requests": [{"number": n} for n in prs],
    }


def fresh(events: list[dict[str, object]], suites: list[dict[str, object]]) -> bool:
    return bool(flowcheck("risk_gate").approval_is_fresh(events, suites, 7, "feat/x"))


# ---- R2-1, R2-4: the freshness of the approval, by times GitHub sets ----------------------------

PUSH = "2026-11-26T10:00:00Z"
LATER = "2026-11-26T11:00:00Z"


def test_a_label_added_before_the_head_commit_arrived_is_not_fresh() -> None:
    assert fresh([labeled("2026-11-26T09:00:00Z")], [suite(PUSH)]) is False


def test_a_label_added_after_the_head_commit_arrived_is_fresh() -> None:
    assert fresh([labeled(LATER)], [suite(PUSH)]) is True


def test_a_label_removed_and_added_again_after_the_push_is_fresh() -> None:
    events = [labeled("2026-11-26T09:00:00Z"), unlabeled("2026-11-26T10:00:05Z"), labeled(LATER)]

    assert fresh(events, [suite(PUSH)]) is True


def test_no_label_or_a_removed_label_is_not_fresh() -> None:
    assert fresh([], [suite(PUSH)]) is False
    assert fresh([labeled(LATER), unlabeled("2026-11-26T12:00:00Z")], [suite(PUSH)]) is False
    assert fresh([labeled(LATER, "needs-work")], [suite(PUSH)]) is False


def test_an_arrival_that_cannot_be_read_is_not_fresh() -> None:
    assert fresh([labeled(LATER)], []) is False
    assert fresh([labeled(LATER)], [suite("not a time")]) is False
    assert fresh([labeled(LATER)], [suite(PUSH, app="some-other-app")]) is False
    assert fresh([labeled(LATER)], [suite(PUSH, branch="other", prs=())]) is False
    assert fresh([labeled("unreadable")], [suite(PUSH)]) is False


def test_commit_dates_never_matter() -> None:
    # Made before the label but pushed after it: the suite is later than the label.
    events = [committed("2026-11-26T08:00:00Z"), labeled("2026-11-26T10:30:00Z")]
    assert fresh(events, [suite(LATER)]) is False
    # Dated in the future, pushed before the label: the approval stands.
    events = [committed("2030-01-01T00:00:00Z"), labeled(LATER)]
    assert fresh(events, [suite(PUSH)]) is True


def test_the_earliest_suite_of_this_pull_request_is_the_arrival_and_another_branchs_is_not() -> (
    None
):
    suites = [
        suite("2026-11-25T00:00:00Z", branch="elsewhere", prs=()),  # pushed there long ago
        suite(LATER),
        suite(PUSH),
    ]

    assert fresh([labeled("2026-11-26T10:30:00Z")], suites) is True
    assert fresh([labeled("2026-11-26T09:30:00Z")], suites) is False


def test_a_suite_of_the_pull_request_found_by_its_branch_counts_when_it_lists_no_pull() -> None:
    assert fresh([labeled(LATER)], [suite(PUSH, prs=())]) is True


def test_a_force_push_after_the_suite_moves_the_arrival() -> None:
    events = [labeled("2026-11-26T10:30:00Z"), force_pushed("2026-11-26T10:45:00Z")]

    assert fresh(events, [suite(PUSH)]) is False


def test_the_readers_take_slurped_and_concatenated_pages() -> None:
    risk_gate = flowcheck("risk_gate")
    page_one, page_two = [committed(PUSH)], [labeled(LATER)]
    for text in (json.dumps([page_one, page_two]), json.dumps(page_one) + json.dumps(page_two)):
        events = risk_gate._timeline_events(text)
        assert [e["event"] for e in events] == ["committed", "labeled"]
    pages = [{"total_count": 2, "check_suites": [suite(PUSH)]}, {"check_suites": [suite(LATER)]}]
    assert len(risk_gate.check_suites_of(json.dumps(pages))) == 2


MIGRATION = "migrations/versions/0002_x.py"


def _gate(
    tmp_path: Path, events: list[dict[str, object]], suites: list[dict[str, object]], *labels: str
) -> tuple[int, str]:
    repo, base = init_repo(tmp_path / "repo", {"README.md": "# x\n"})
    head = commit(repo, {MIGRATION: "# m\n"}, "F99 Migration")
    rules = tmp_path / "paths.toml"
    rules.write_text('[[rule]]\nglob = "migrations/**"\nlevel = "High"\n', encoding="utf-8")
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps([events]), encoding="utf-8")
    check_suites = tmp_path / "check-suites.json"
    check_suites.write_text(json.dumps([{"check_suites": suites}]), encoding="utf-8")
    args = ["--rules", str(rules), "--repo", str(repo), "--base", base, "--head", head]
    for label in labels:
        args += ["--label", label]
    extra = ["--timeline-file", str(timeline), "--check-suites-file", str(check_suites)]
    result = run_cli("risk_gate", *args, *extra, "--pr-number", "7", "--head-ref", "feat/x")
    return result.returncode, output(result)


def test_the_gate_command_ignores_a_stale_label_and_names_the_high_path(tmp_path: Path) -> None:
    code, out = _gate(tmp_path, [labeled("2026-11-26T09:00:00Z")], [suite(PUSH)], LABEL)

    assert code != 0 and MIGRATION in out and "does not count" in out


def test_the_gate_command_accepts_a_fresh_label(tmp_path: Path) -> None:
    code, out = _gate(tmp_path, [labeled(LATER)], [suite(PUSH)], LABEL)

    assert code == 0, out


def test_the_gate_command_does_not_count_a_label_when_no_suite_can_be_read(tmp_path: Path) -> None:
    code, out = _gate(tmp_path, [labeled(LATER)], [], LABEL)

    assert code != 0 and MIGRATION in out


# ---- R2-2: any conftest.py ---------------------------------------------------------------------

LOCKED = "tests/test_calc.py"
TESTS = "from calc import add\n\n\ndef test_adds():\n    assert add(2, 3) == 5\n"


@pytest.mark.parametrize(
    ("path", "existing"),
    [
        ("conftest.py", False),
        ("tests/conftest.py", False),
        ("tests/unit/conftest.py", False),
        ("tests/conftest.py", True),
        ("app/trips/conftest.py", False),
    ],
)
def test_lock_a_commit_without_the_trailer_that_adds_or_changes_a_conftest_breaks_it(
    tmp_path: Path, path: str, existing: bool
) -> None:
    test_lock = flowcheck("test_lock")
    # An existing conftest.py that no test commit of this branch touched (it comes from main).
    base_files = {"README.md": "# x\n", "pytest.ini": "[pytest]\n"}
    if existing:
        base_files[path] = "X = 1\n"
    repo, base = init_repo(tmp_path / "repo", base_files)
    commit(repo, {LOCKED: TESTS}, "F99 Tests", test=True)
    head = commit(
        repo, {path: "def pytest_collection_modifyitems(items):\n    items.clear()\n"}, "F99 Code"
    )

    assert test_lock.check(repo, base, head) == [path]
    result = run_cli("test_lock", "--repo", str(repo), "--base", base, "--head", head)
    assert result.returncode != 0 and path in output(result)


def test_lock_a_commit_without_the_trailer_that_deletes_a_conftest_breaks_it(
    tmp_path: Path,
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = init_repo(
        tmp_path / "repo", {"README.md": "# x\n", "tests/conftest.py": "X = 1\n"}
    )
    commit(repo, {LOCKED: TESTS}, "F99 Tests", test=True)
    head = commit(repo, {"tests/conftest.py": None}, "F99 Remove it")  # type: ignore[dict-item]

    assert test_lock.check(repo, base, head) == ["tests/conftest.py"]


def test_lock_a_test_commit_may_change_a_conftest(tmp_path: Path) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = init_repo(tmp_path / "repo", {"README.md": "# x\n"})
    commit(repo, {LOCKED: TESTS, "tests/conftest.py": "X = 1\n"}, "F99 Tests", test=True)
    head = commit(repo, {"tests/conftest.py": "X = 2\n"}, "F99 Fix the fixture", test=True)

    assert test_lock.check(repo, base, head) == []


# ---- the workflows -------------------------------------------------------------------------------

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def test_the_gate_reads_the_timeline_with_read_scope_and_a_push_is_never_cancelled_by_an_edit() -> (
    None
):
    reusable = (WORKFLOWS / "risk-gate-check.yml").read_text(encoding="utf-8")
    caller = (WORKFLOWS / "risk-gate.yml").read_text(encoding="utf-8")
    gate = reusable[reusable.index("  risk-gate:") :]

    assert "pull-requests: read" in gate and "checks: read" in gate
    assert "pull-requests: write" not in gate and "check-suites" in gate
    assert "--timeline-file" in gate and "timeline?per_page=100" in gate
    assert "github.event.action == 'synchronize' && 'push' || 'other'" in caller
    assert "HTTP 404" in reusable


# ---- R2-3: a merge commit cannot change a conftest.py ---------------------------------------------


def _evil_merge(repo: Path, path: str) -> str:
    git = flowcheck("_git").git
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, {"side.txt": "s"}, "F99 Side work")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"calc.py": CODE_MAIN}, "F99 Main work")
    git(repo, "merge", "-q", "--no-ff", "--no-commit", "side")
    (repo / path).write_text("def pytest_collection_modifyitems(items):\n    items.clear()\n")
    git(repo, "add", path)
    git(repo, "commit", "-q", "-m", "Merge side")
    return git(repo, "rev-parse", "HEAD").decode().strip()


CODE_MAIN = "def add(a, b):\n    return a + b\n"


@pytest.mark.parametrize("touched_by_a_test_commit", [True, False])
def test_lock_a_merge_commit_that_edits_a_conftest_breaks_it(
    tmp_path: Path, touched_by_a_test_commit: bool
) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = init_repo(
        tmp_path / "repo", {"README.md": "# x\n", "tests/conftest.py": "X = 1\n"}
    )
    files = {LOCKED: TESTS}
    if touched_by_a_test_commit:
        files["tests/conftest.py"] = "X = 2\n"
    commit(repo, files, "F99 Tests", test=True)

    head = _evil_merge(repo, "tests/conftest.py")

    assert test_lock.check(repo, base, head) == ["tests/conftest.py"]
    result = run_cli("test_lock", "--repo", str(repo), "--base", base, "--head", head)
    assert result.returncode != 0 and "tests/conftest.py" in output(result)


def test_lock_a_merge_that_changes_no_conftest_passes(tmp_path: Path) -> None:
    test_lock = flowcheck("test_lock")
    git = flowcheck("_git").git
    repo, base = init_repo(tmp_path / "repo", {"README.md": "# x\n"})
    commit(repo, {LOCKED: TESTS}, "F99 Tests", test=True)
    git(repo, "checkout", "-q", "-b", "side")
    commit(repo, {"side.txt": "s"}, "F99 Side work")
    git(repo, "checkout", "-q", "main")
    commit(repo, {"calc.py": CODE_MAIN}, "F99 Main work")
    git(repo, "merge", "-q", "--no-ff", "-m", "Merge side", "side")
    head = git(repo, "rev-parse", "HEAD").decode().strip()

    assert test_lock.check(repo, base, head) == []


def test_lock_a_merge_that_adds_a_conftest_nobody_had_breaks_it(tmp_path: Path) -> None:
    test_lock = flowcheck("test_lock")
    repo, base = init_repo(tmp_path / "repo", {"README.md": "# x\n"})
    commit(repo, {LOCKED: TESTS}, "F99 Tests", test=True)
    (repo / "tests" / "unit").mkdir(parents=True)

    head = _evil_merge(repo, "tests/unit/conftest.py")

    assert test_lock.check(repo, base, head) == ["tests/unit/conftest.py"]
