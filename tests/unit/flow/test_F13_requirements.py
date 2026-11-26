"""F13 Requirement-to-test report, cases F13-1 to F13-5 (epic-11 spec, section 2.12; task scope, version 1.5).

The check reads `tests/requirement-ids.txt` (the P0 IDs in scope) and the JUnit XML files
that pytest writes with `--junitxml` in the `unit` and `integration` jobs. Every ID file
and JUnit file here is built under `tmp_path`; nothing depends on the repository's own tests.
The interface is in tests/INTERFACE.md, section "F13".
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest
import yaml

from tests.support import REPO_ROOT
from tests.unit.flow.repo import flowcheck, output, run_cli

REAL_IDS_FILE = REPO_ROOT / "tests" / "requirement-ids.txt"
REPORT_MD = "requirements-report.md"
REPORT_JSON = "requirements-report.json"

UNIT_AUTH = "tests.unit.identity.test_passwords"
INT_AUTH = "tests.integration.identity.test_sign_in"
INT_MONEY = "tests.integration.money.test_splits"
UNIT_FLOW = "tests.unit.platform.test_config"
UNIT_CLASS = "tests.unit.identity.test_tokens.TestThing"
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"
NOT_CANCELLED = "${{ !cancelled() }}"
EARLIER_SUMMARY = "## An earlier step\n\nThis text was here before the report.\n"

# (classname, name, outcome); outcome is "passed", "failed", "error", or "skipped".
Case = tuple[str, str, str]

_CHILD = {
    "passed": "",
    "failed": '<failure message="assert 1 == 2">assert 1 == 2</failure>',
    "error": '<error message="failed on setup">fixture not found</error>',
    "skipped": '<skipped type="pytest.skip" message="not yet">not yet</skipped>',
}


def _junit(path: Path, cases: list[Case]) -> Path:
    """Write a JUnit XML file in the shape pytest's `--junitxml` writes."""
    lines = []
    for classname, name, outcome in cases:
        child = _CHILD[outcome]
        attrs = f'classname={quoteattr(classname)} name={quoteattr(name)} time="0.010"'
        lines.append(f"<testcase {attrs}>{child}</testcase>" if child else f"<testcase {attrs} />")
    count = {kind: sum(1 for *_, o in cases if o == kind) for kind in _CHILD}
    path.write_text(
        '<?xml version="1.0" encoding="utf-8"?>'
        '<testsuites name="pytest tests">'
        f'<testsuite name="pytest" errors="{count["error"]}" failures="{count["failed"]}" '
        f'skipped="{count["skipped"]}" tests="{len(cases)}" time="0.5" '
        'timestamp="2026-11-26T10:00:00.000000+00:00" hostname="runner">'
        + "".join(lines)
        + "</testsuite></testsuites>",
        encoding="utf-8",
    )
    return path


def _ids(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def _label(classname: str, name: str) -> str:
    return f"{classname}::{name}"


def _run(ids_file: Path, junit_files: list[Path], out: Path) -> tuple[int, str]:
    args = ["--ids", str(ids_file)]
    for junit in junit_files:
        args += ["--junit", str(junit)]
    result = run_cli("requirements", *args, "--out", str(out))
    return result.returncode, output(result)


def _report_json(out: Path) -> dict[str, object]:
    path = out / REPORT_JSON
    assert path.is_file(), f"{path} was not written"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _sorted_ids(data: dict[str, object]) -> dict[str, list[str]]:
    ids = data["ids"]
    assert isinstance(ids, dict)
    return {key: sorted(value) for key, value in ids.items()}


def _earlier_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A job summary file that already holds an earlier step's text."""
    summary = tmp_path / "step-summary.md"
    summary.write_text(EARLIER_SUMMARY, encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    return summary


def _appended_summary(summary: Path) -> str:
    """What the check appended to the job summary; the earlier text must be kept as it was."""
    text = summary.read_text(encoding="utf-8")
    assert text.startswith(EARLIER_SUMMARY), "the job summary was overwritten, not appended to"
    return text[len(EARLIER_SUMMARY) :]


def test_F13_1_the_report_lists_each_id_in_scope_with_its_tests(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = flowcheck("requirements")
    ids_file = _ids(
        tmp_path / "requirement-ids.txt",
        "# P0 IDs in scope\n\nAUTH-01\n   # an indented comment\nEXP-04  # remainder rule\n\n",
    )
    unit = _junit(
        tmp_path / "unit.xml",
        [
            (UNIT_AUTH, "test_AUTH_01_password_is_stored_as_an_argon2id_hash", "passed"),
            # Card case IDs and tests with no ID are not read as requirement IDs.
            (UNIT_FLOW, "test_F1_4_missing_required_setting_stops_the_process", "passed"),
            (UNIT_FLOW, "test_CT1_2_contract_version_is_pinned", "passed"),
            (UNIT_FLOW, "test_F13_2_a_card_case_id_with_two_parts", "passed"),
            (UNIT_FLOW, "test_settings_are_read_once", "passed"),
            # The ID right after `test_`, ended by `_`, `[`, or the end of the name.
            (UNIT_AUTH, "test_AUTH_01[param-a]", "passed"),
            (UNIT_AUTH, "test_AUTH_01", "passed"),
            (UNIT_CLASS, "test_AUTH_01_a_method_of_a_test_class", "passed"),
            # No ID: not right after `test_`, not capital letters, not exactly two digits.
            (UNIT_FLOW, "test_login_AUTH_01_x", "passed"),
            (UNIT_FLOW, "test_step_01_x", "passed"),
            (UNIT_FLOW, "test_AUTH_1_x", "passed"),
            (UNIT_FLOW, "test_AUTH_012_x", "passed"),
        ],
    )
    integration = _junit(
        tmp_path / "integration.xml",
        [
            (INT_AUTH, "test_AUTH_01_sign_in_attempts_are_rate_limited", "passed"),
            (INT_MONEY, "test_EXP_04_remainder_goes_by_join_order", "passed"),
        ],
    )
    expected = {
        "AUTH-01": sorted(
            [
                _label(UNIT_AUTH, "test_AUTH_01_password_is_stored_as_an_argon2id_hash"),
                _label(INT_AUTH, "test_AUTH_01_sign_in_attempts_are_rate_limited"),
                _label(UNIT_AUTH, "test_AUTH_01[param-a]"),
                _label(UNIT_AUTH, "test_AUTH_01"),
                _label(UNIT_CLASS, "test_AUTH_01_a_method_of_a_test_class"),
            ]
        ),
        "EXP-04": [_label(INT_MONEY, "test_EXP_04_remainder_goes_by_join_order")],
    }

    assert requirements.load_ids(ids_file) == ["AUTH-01", "EXP-04"]

    report = requirements.check(ids_file, [unit, integration])
    assert {key: sorted(value) for key, value in report.tests_by_id.items()} == expected
    assert list(report.missing) == []
    assert list(report.unknown) == []

    summary = tmp_path / "step-summary.md"
    summary.write_text("", encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    out = tmp_path / "out"
    code, text = _run(ids_file, [unit, integration], out)
    assert code == 0, text

    data = _report_json(out)
    assert _sorted_ids(data) == expected
    assert data["missing"] == []
    assert data["unknown"] == []

    markdown = (out / REPORT_MD).read_text(encoding="utf-8")
    for requirement, tests in expected.items():
        assert requirement in markdown
        for test in tests:
            assert test in markdown
    for ignored in (
        "test_F1_4_",
        "test_CT1_2_",
        "test_F13_2_",
        "test_settings_are_read_once",
        "test_login_AUTH_01_x",
        "test_step_01_x",
        "test_AUTH_1_x",
        "test_AUTH_012_x",
    ):
        assert ignored not in markdown
    assert markdown.strip() in summary.read_text(encoding="utf-8")


def test_F13_2_a_p0_id_in_scope_with_no_test_fails_the_check_and_names_the_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = flowcheck("requirements")
    ids_file = _ids(tmp_path / "requirement-ids.txt", "AUTH-01\nAUTH-02\n")
    unit = _junit(
        tmp_path / "unit.xml",
        [(UNIT_AUTH, "test_AUTH_01_password_is_stored_as_an_argon2id_hash", "passed")],
    )

    report = requirements.check(ids_file, [unit])
    assert list(report.missing) == ["AUTH-02"]
    assert report.tests_by_id["AUTH-02"] == []
    assert list(report.unknown) == []

    # The job summary already holds an earlier step's text: the report is appended to it.
    summary = _earlier_summary(tmp_path, monkeypatch)
    out = tmp_path / "out"
    code, text = _run(ids_file, [unit], out)
    assert code != 0, text
    assert "AUTH-02" in text
    data = _report_json(out)
    assert data["missing"] == ["AUTH-02"]
    assert "AUTH-02" in (out / REPORT_MD).read_text(encoding="utf-8")
    assert "AUTH-02" in _appended_summary(summary)


def test_F13_3_an_id_of_an_epic_not_in_scope_yet_is_not_asked_for(tmp_path: Path) -> None:
    requirements = flowcheck("requirements")
    # EXP-04 is a P0 ID of prd.md, but its epic is not in scope: it is not in the list.
    ids_file = _ids(tmp_path / "requirement-ids.txt", "# EPIC-01\nAUTH-01\n")
    unit = _junit(
        tmp_path / "unit.xml",
        [
            (UNIT_AUTH, "test_AUTH_01_password_is_stored_as_an_argon2id_hash", "passed"),
            (UNIT_FLOW, "test_F1_4_missing_required_setting_stops_the_process", "passed"),
        ],
    )

    report = requirements.check(ids_file, [unit])
    assert set(report.tests_by_id) == {"AUTH-01"}
    assert list(report.missing) == []
    assert list(report.unknown) == []

    out = tmp_path / "out"
    code, text = _run(ids_file, [unit], out)
    assert code == 0, text
    assert "EXP-04" not in text
    assert "EXP-04" not in (out / REPORT_MD).read_text(encoding="utf-8")
    assert set(_sorted_ids(_report_json(out))) == {"AUTH-01"}

    # The repository's own list is in the format the check reads (EPIC-11 lists no ID yet).
    assert REAL_IDS_FILE.is_file(), f"{REAL_IDS_FILE} does not exist"
    for requirement in requirements.load_ids(REAL_IDS_FILE):
        assert requirement.count("-") == 1, requirement

    # A line that is not an ID, after comments are removed, is an error of the file:
    # the command exits with 2 and names the line number (here line 3).
    bad_ids = tmp_path / "bad" / "requirement-ids.txt"
    bad_ids.parent.mkdir()
    _ids(bad_ids, "# EPIC-01\nAUTH-01\nauth_01  # not an ID\n")
    code, text = _run(bad_ids, [unit], tmp_path / "bad-out")
    assert code == 2, text
    assert re.search(r"(?i:line)\s*3\b|:3\b", text), f"the line number 3 is not named:\n{text}"


def test_F13_4_a_test_named_with_an_id_not_in_the_list_fails_the_check_and_names_the_test(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    requirements = flowcheck("requirements")
    ids_file = _ids(tmp_path / "requirement-ids.txt", "AUTH-01\n")
    unit = _junit(
        tmp_path / "unit.xml",
        [(UNIT_AUTH, "test_AUTH_01_password_is_stored_as_an_argon2id_hash", "passed")],
    )
    integration = _junit(
        tmp_path / "integration.xml",
        [
            (INT_MONEY, "test_EXP_04_remainder_goes_by_join_order", "passed"),
            # A skipped test named with an ID not in the list is still an unknown test.
            (INT_MONEY, "test_EXP_05_settle_up_is_not_built_yet", "skipped"),
        ],
    )
    stray = _label(INT_MONEY, "test_EXP_04_remainder_goes_by_join_order")
    skipped_stray = _label(INT_MONEY, "test_EXP_05_settle_up_is_not_built_yet")

    report = requirements.check(ids_file, [unit, integration])
    assert sorted(report.unknown) == sorted([stray, skipped_stray])
    assert list(report.missing) == []
    assert set(report.tests_by_id) == {"AUTH-01"}

    summary = _earlier_summary(tmp_path, monkeypatch)
    out = tmp_path / "out"
    code, text = _run(ids_file, [unit, integration], out)
    assert code != 0, text
    assert "test_EXP_04_remainder_goes_by_join_order" in text
    assert "test_EXP_05_settle_up_is_not_built_yet" in text
    unknown = _report_json(out)["unknown"]
    assert isinstance(unknown, list)
    assert sorted(unknown) == sorted([stray, skipped_stray])
    markdown = (out / REPORT_MD).read_text(encoding="utf-8")
    assert stray in markdown
    assert skipped_stray in markdown
    appended = _appended_summary(summary)
    assert "test_EXP_04_remainder_goes_by_join_order" in appended
    assert "test_EXP_05_settle_up_is_not_built_yet" in appended


def test_F13_5_an_id_whose_only_test_is_skipped_counts_as_having_no_test(tmp_path: Path) -> None:
    requirements = flowcheck("requirements")
    ids_file = _ids(tmp_path / "requirement-ids.txt", "AUTH-01\nAUTH-02\nAUTH-03\n")
    unit = _junit(
        tmp_path / "unit.xml",
        [
            # AUTH-01: its only test is skipped.
            (UNIT_AUTH, "test_AUTH_01_password_is_stored_as_an_argon2id_hash", "skipped"),
            # AUTH-02: one skipped and one failed test; the failed one is still a test of it.
            (UNIT_AUTH, "test_AUTH_02_refresh_token_is_replaced", "skipped"),
            (UNIT_AUTH, "test_AUTH_02_replaced_token_revokes_the_session", "failed"),
        ],
    )
    integration = _junit(
        tmp_path / "integration.xml",
        # AUTH-03: one test that errored; it is still a test of it.
        [(INT_AUTH, "test_AUTH_03_user_edits_only_their_own_profile", "error")],
    )

    report = requirements.check(ids_file, [unit, integration])
    assert list(report.missing) == ["AUTH-01"]
    assert report.tests_by_id["AUTH-01"] == []
    assert list(report.tests_by_id["AUTH-02"]) == [
        _label(UNIT_AUTH, "test_AUTH_02_replaced_token_revokes_the_session")
    ]
    assert list(report.tests_by_id["AUTH-03"]) == [
        _label(INT_AUTH, "test_AUTH_03_user_edits_only_their_own_profile")
    ]
    assert list(report.unknown) == []

    out = tmp_path / "out"
    code, text = _run(ids_file, [unit, integration], out)
    assert code != 0, text
    assert "AUTH-01" in text
    assert _report_json(out)["missing"] == ["AUTH-01"]


def _step_args(run: str) -> str:
    """A step's `run` text on one line, with line continuations joined."""
    return " ".join(run.replace("\\\n", " ").split())


def _uploads(job: dict[str, object]) -> list[dict[str, object]]:
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return [
        step
        for step in steps
        if isinstance(step, dict)
        and str(step.get("uses", "")).startswith("actions/upload-artifact@")
    ]


def _runs(job: dict[str, object]) -> list[str]:
    steps = job.get("steps", [])
    assert isinstance(steps, list)
    return [
        _step_args(str(step["run"])) for step in steps if isinstance(step, dict) and "run" in step
    ]


def _condition(value: object) -> str:
    return " ".join(str(value).split())


def test_F13_1_ci_keeps_the_report_with_each_run() -> None:
    workflow = yaml.safe_load(CI_WORKFLOW.read_text(encoding="utf-8"))
    assert isinstance(workflow, dict)
    jobs = workflow["jobs"]
    assert isinstance(jobs, dict)

    # `unit` and `integration` write JUnit XML and keep it, unless the run was cancelled.
    for name in ("unit", "integration"):
        job = jobs[name]
        junit_files = [
            match.group(1)
            for run in _runs(job)
            if "pytest" in run
            for match in [re.search(r"--junitxml[= ](\S+)", run)]
            if match
        ]
        assert len(junit_files) == 1, f"job {name} does not run pytest with --junitxml"
        kept = [
            step
            for step in _uploads(job)
            if junit_files[0] in str(step.get("with", {}).get("path", ""))  # type: ignore[union-attr]
        ]
        assert kept, f"job {name} does not upload {junit_files[0]}"
        assert _condition(kept[0].get("if")) == NOT_CANCELLED, f"job {name}: {kept[0].get('if')}"

    # `requirements` runs after both, when a test job failed but not when the run was cancelled.
    job = jobs["requirements"]
    needs = job["needs"]
    needs = [needs] if isinstance(needs, str) else needs
    assert {"unit", "integration"} <= set(needs)
    assert _condition(job.get("if")) == NOT_CANCELLED, job.get("if")

    checks = [run for run in _runs(job) if "python -m flowchecks.requirements" in run]
    assert len(checks) == 1, "the requirements job does not run python -m flowchecks.requirements"
    check = checks[0]
    assert "--ids tests/requirement-ids.txt" in check
    assert len(re.findall(r"--junit[= ]\S+", check)) == 2, check
    out = re.search(r"--out[= ](\S+)", check)
    assert out, check

    reports = [
        step
        for step in _uploads(job)
        if step.get("with", {}).get("name") == "requirements-report"  # type: ignore[union-attr]
    ]
    assert reports, "the requirements job does not upload requirements-report"
    assert out.group(1) in str(reports[0]["with"]["path"])  # type: ignore[index]
    assert _condition(reports[0].get("if")) == NOT_CANCELLED, reports[0].get("if")
