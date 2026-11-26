"""F13 Requirement-to-test report, cases F13-1 to F13-5 (epic-11 spec, section 2.12; task scope, version 1.5).

The check reads `tests/requirement-ids.txt` (the P0 IDs in scope) and the JUnit XML files
that pytest writes with `--junitxml` in the `unit` and `integration` jobs. Every ID file
and JUnit file here is built under `tmp_path`; nothing depends on the repository's own tests.
The interface is in tests/INTERFACE.md, section "F13".
"""

from __future__ import annotations

import json
from pathlib import Path
from xml.sax.saxutils import quoteattr

import pytest

from tests.support import REPO_ROOT
from tests.unit.flow.repo import flowcheck, output, run_cli

REAL_IDS_FILE = REPO_ROOT / "tests" / "requirement-ids.txt"
REPORT_MD = "requirements-report.md"
REPORT_JSON = "requirements-report.json"

UNIT_AUTH = "tests.unit.identity.test_passwords"
INT_AUTH = "tests.integration.identity.test_sign_in"
INT_MONEY = "tests.integration.money.test_splits"
UNIT_FLOW = "tests.unit.platform.test_config"

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
    for ignored in ("test_F1_4_", "test_CT1_2_", "test_F13_2_", "test_settings_are_read_once"):
        assert ignored not in markdown
    assert markdown.strip() in summary.read_text(encoding="utf-8")


def test_F13_2_a_p0_id_in_scope_with_no_test_fails_the_check_and_names_the_id(
    tmp_path: Path,
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

    out = tmp_path / "out"
    code, text = _run(ids_file, [unit], out)
    assert code != 0, text
    assert "AUTH-02" in text
    data = _report_json(out)
    assert data["missing"] == ["AUTH-02"]
    assert "AUTH-02" in (out / REPORT_MD).read_text(encoding="utf-8")


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


def test_F13_4_a_test_named_with_an_id_not_in_the_list_fails_the_check_and_names_the_test(
    tmp_path: Path,
) -> None:
    requirements = flowcheck("requirements")
    ids_file = _ids(tmp_path / "requirement-ids.txt", "AUTH-01\n")
    unit = _junit(
        tmp_path / "unit.xml",
        [(UNIT_AUTH, "test_AUTH_01_password_is_stored_as_an_argon2id_hash", "passed")],
    )
    integration = _junit(
        tmp_path / "integration.xml",
        [(INT_MONEY, "test_EXP_04_remainder_goes_by_join_order", "passed")],
    )
    stray = _label(INT_MONEY, "test_EXP_04_remainder_goes_by_join_order")

    report = requirements.check(ids_file, [unit, integration])
    assert list(report.unknown) == [stray]
    assert list(report.missing) == []

    out = tmp_path / "out"
    code, text = _run(ids_file, [unit, integration], out)
    assert code != 0, text
    assert "test_EXP_04_remainder_goes_by_join_order" in text
    assert _report_json(out)["unknown"] == [stray]
    assert stray in (out / REPORT_MD).read_text(encoding="utf-8")


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
