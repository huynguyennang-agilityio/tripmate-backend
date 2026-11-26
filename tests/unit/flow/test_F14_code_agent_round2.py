"""F14 Flow checks: review round 2 fixes (code agent's own tests, own file).

R2-1: `human-approved` counts only when added after the newest commit reached the pull request.
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


def committed(at: str, *, author: str | None = None) -> dict[str, object]:
    return {
        "event": "committed",
        "committer": {"date": at},
        "author": {"date": author or at},
    }


def force_pushed(at: str) -> dict[str, object]:
    return {"event": "head_ref_force_pushed", "created_at": at}


# ---- R2-1: the freshness of the approval ------------------------------------------------------


def test_a_label_added_before_the_newest_commit_is_not_fresh() -> None:
    risk_gate = flowcheck("risk_gate")
    events = [
        committed("2026-11-26T09:00:00Z"),
        labeled("2026-11-26T10:00:00Z"),
        committed("2026-11-26T11:00:00Z"),
    ]

    assert risk_gate.approval_is_fresh(events) is False


def test_a_label_added_after_the_newest_commit_is_fresh() -> None:
    risk_gate = flowcheck("risk_gate")
    events = [
        committed("2026-11-26T09:00:00Z"),
        committed("2026-11-26T11:00:00Z"),
        labeled("2026-11-26T12:00:00Z"),
    ]

    assert risk_gate.approval_is_fresh(events) is True


def test_a_label_removed_and_added_again_after_the_push_is_fresh() -> None:
    risk_gate = flowcheck("risk_gate")
    events = [
        committed("2026-11-26T09:00:00Z"),
        labeled("2026-11-26T10:00:00Z"),
        force_pushed("2026-11-26T11:00:00Z"),
        unlabeled("2026-11-26T11:00:05Z"),
        labeled("2026-11-26T12:00:00Z"),
    ]

    assert risk_gate.approval_is_fresh(events) is True


def test_no_label_or_a_removed_label_is_not_fresh() -> None:
    risk_gate = flowcheck("risk_gate")

    assert risk_gate.approval_is_fresh([committed("2026-11-26T09:00:00Z")]) is False
    assert risk_gate.approval_is_fresh([]) is False
    removed = [labeled("2026-11-26T10:00:00Z"), unlabeled("2026-11-26T10:30:00Z")]
    assert risk_gate.approval_is_fresh(removed) is False
    other = [labeled("2026-11-26T10:00:00Z", "needs-work")]
    assert risk_gate.approval_is_fresh(other) is False


def test_a_force_push_after_the_label_makes_it_stale_even_with_an_old_commit_date() -> None:
    risk_gate = flowcheck("risk_gate")
    events = [
        labeled("2026-11-26T10:00:00Z"),
        force_pushed("2026-11-26T10:30:00Z"),
        committed("2026-11-26T08:00:00Z"),
    ]

    assert risk_gate.approval_is_fresh(events) is False


def test_times_with_an_offset_are_compared_as_times_and_the_later_date_of_a_commit_counts() -> None:
    risk_gate = flowcheck("risk_gate")
    # 12:00+02:00 is 10:00Z; the author date is the later one.
    events = [
        committed("2026-11-26T09:00:00Z", author="2026-11-26T12:00:00+02:00"),
        labeled("2026-11-26T10:00:00Z"),
    ]

    assert risk_gate.approval_is_fresh(events) is False
    assert risk_gate.approval_is_fresh([*events[:1], labeled("2026-11-26T10:00:01Z")]) is True


def test_a_label_with_an_unreadable_time_is_not_fresh() -> None:
    risk_gate = flowcheck("risk_gate")

    assert risk_gate.approval_is_fresh([labeled("yesterday")]) is False


def test_the_timeline_reader_takes_slurped_and_concatenated_pages() -> None:
    risk_gate = flowcheck("risk_gate")
    page_one, page_two = [committed("2026-11-26T09:00:00Z")], [labeled("2026-11-26T10:00:00Z")]

    slurped = json.dumps([page_one, page_two])
    concatenated = json.dumps(page_one) + json.dumps(page_two)
    for text in (slurped, concatenated):
        events = risk_gate._timeline_events(text)
        assert [e["event"] for e in events] == ["committed", "labeled"]


MIGRATION = "migrations/versions/0002_x.py"


def _gate(tmp_path: Path, events: list[dict[str, object]], *labels: str) -> tuple[int, str]:
    repo, base = init_repo(tmp_path / "repo", {"README.md": "# x\n"})
    head = commit(repo, {MIGRATION: "# m\n"}, "F99 Migration")
    rules = tmp_path / "paths.toml"
    rules.write_text('[[rule]]\nglob = "migrations/**"\nlevel = "High"\n', encoding="utf-8")
    timeline = tmp_path / "timeline.json"
    timeline.write_text(json.dumps([events]), encoding="utf-8")
    args = ["--rules", str(rules), "--repo", str(repo), "--base", base, "--head", head]
    for label in labels:
        args += ["--label", label]
    result = run_cli("risk_gate", *args, "--timeline-file", str(timeline))
    return result.returncode, output(result)


def test_the_gate_command_ignores_a_stale_label_and_names_the_high_path(tmp_path: Path) -> None:
    stale = [labeled("2026-11-26T10:00:00Z"), committed("2026-11-26T11:00:00Z")]

    code, out = _gate(tmp_path, stale, LABEL)

    assert code != 0 and MIGRATION in out and "does not count" in out


def test_the_gate_command_accepts_a_fresh_label(tmp_path: Path) -> None:
    fresh = [committed("2026-11-26T11:00:00Z"), labeled("2026-11-26T12:00:00Z")]

    code, out = _gate(tmp_path, fresh, LABEL)

    assert code == 0, out


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

    assert "pull-requests: read" in gate and "pull-requests: write" not in gate
    assert "--timeline-file" in gate and "timeline?per_page=100" in gate
    assert "github.event.action == 'synchronize' && 'push' || 'other'" in caller
    assert "HTTP 404" in reusable
