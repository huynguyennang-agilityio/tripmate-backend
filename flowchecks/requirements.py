"""Requirement-to-test report: every P0 ID in scope has a test, every ID in a test name is in scope.

The ID file (`tests/requirement-ids.txt`) lists one requirement ID per line (`AUTH-01`); text
from `#` to the end of a line is a comment. The tests are read from JUnit XML files (pytest's
`--junitxml`). A test's ID is the one right after `test_` in its name (`test_AUTH_01_...`).
A skipped test counts for no ID, and is not a test of the report; a failed or errored one does.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

ID_FORM = re.compile(r"[A-Z]+-[0-9]{2}")
_TEST_ID = re.compile(r"test_([A-Z]+)_([0-9]{2})(?:[_\[]|$)")

REPORT_MD = "requirements-report.md"
REPORT_JSON = "requirements-report.json"


@dataclass(frozen=True)
class Report:
    tests_by_id: Mapping[str, Sequence[str]]
    missing: Sequence[str]
    unknown: Sequence[str]

    @property
    def passed(self) -> bool:
        return not self.missing and not self.unknown


def load_ids(path: str | os.PathLike[str]) -> list[str]:
    """The IDs of an ID file, in file order. Raises ValueError naming a line that is not an ID."""
    ids: list[str] = []
    text = Path(path).read_text(encoding="utf-8")
    for number, line in enumerate(text.splitlines(), start=1):
        entry = line.split("#", 1)[0].strip()
        if not entry:
            continue
        if not ID_FORM.fullmatch(entry):
            raise ValueError(f"{path}: line {number} is not a requirement ID: {entry!r}")
        if entry not in ids:
            ids.append(entry)
    return ids


def _tests(junit_files: Iterable[str | os.PathLike[str]]) -> list[tuple[str, str, bool]]:
    """(label, ID or "", skipped) of every testcase of the files."""
    found: list[tuple[str, str, bool]] = []
    for junit in junit_files:
        # The files are written by this repository's own pytest runs (stdlib only).
        for case in ET.parse(junit).getroot().iter("testcase"):  # noqa: S314
            label = f"{case.get('classname', '')}::{case.get('name', '')}"
            match = _TEST_ID.match(case.get("name", ""))
            requirement = f"{match.group(1)}-{match.group(2)}" if match else ""
            found.append((label, requirement, case.find("skipped") is not None))
    return found


def check(
    ids_file: str | os.PathLike[str], junit_files: Iterable[str | os.PathLike[str]]
) -> Report:
    ids = load_ids(ids_file)
    tests_by_id: dict[str, list[str]] = {requirement: [] for requirement in ids}
    unknown: list[str] = []
    for label, requirement, skipped in _tests(junit_files):
        if not requirement:
            continue
        if requirement not in tests_by_id:
            # A wrong name is wrong whether or not the test runs.
            unknown.append(label)
        elif not skipped:
            tests_by_id[requirement].append(label)
    missing = [requirement for requirement, tests in tests_by_id.items() if not tests]
    return Report(tests_by_id, missing, unknown)


def to_json(report: Report) -> str:
    return json.dumps(
        {
            "ids": {key: list(value) for key, value in report.tests_by_id.items()},
            "missing": list(report.missing),
            "unknown": list(report.unknown),
        },
        indent=2,
    )


def to_markdown(report: Report) -> str:
    lines = ["## Requirement-to-test report", ""]
    if not report.tests_by_id:
        lines.append("No requirement IDs in scope yet.")
    else:
        covered = len(report.tests_by_id) - len(report.missing)
        lines += [
            f"{covered} of {len(report.tests_by_id)} requirement IDs in scope have a test; "
            f"{len(report.missing)} missing, {len(report.unknown)} unknown test(s).",
            "",
            "| ID | Tests |",
            "|----|-------|",
        ]
        for requirement, tests in report.tests_by_id.items():
            cell = "<br>".join(f"`{test}`" for test in tests) if tests else "none"
            lines.append(f"| {requirement} | {cell} |")
    if report.missing:
        lines += ["", "### Missing IDs (no test that ran)", ""]
        lines += [f"- {requirement}" for requirement in report.missing]
    if report.unknown:
        lines += ["", "### Unknown tests (named with an ID not in the list)", ""]
        lines += [f"- `{test}`" for test in report.unknown]
    return "\n".join(lines) + "\n"


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--junit", action="append", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    try:
        report = check(args.ids, args.junit)
    except (ValueError, OSError, ET.ParseError) as error:
        print(f"Requirements: {error}", file=sys.stderr)
        return 2
    markdown = to_markdown(report)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / REPORT_MD).write_text(markdown, encoding="utf-8")
    (out / REPORT_JSON).write_text(to_json(report) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(markdown)
    for requirement in report.missing:
        print(f"Requirements: {requirement} has no test that ran (a skipped test does not count)")
    for test in report.unknown:
        print(f"Requirements: {test} is named with a requirement ID that is not in {args.ids}")
    return 0 if report.passed else 1


if __name__ == "__main__":
    sys.exit(main())
