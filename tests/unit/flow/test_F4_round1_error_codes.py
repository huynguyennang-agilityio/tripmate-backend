"""F4 round 1: tests of the code agent for the error code text check (own file)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from tests.support import REPO_ROOT
from tests.unit.flow.repo import flowcheck


def _write(translations: Path, lang: str, texts: dict[str, str]) -> None:
    folder = translations / lang
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "x.json").write_text(json.dumps(texts), encoding="utf-8")


def test_F4_r1_empty_or_blank_text_counts_as_missing(tmp_path) -> None:
    _write(tmp_path, "en", {"x.a": "A", "x.b": "B", "x.c": "C"})
    _write(tmp_path, "vi", {"x.a": "", "x.b": "   \n", "x.c": "C"})

    assert flowcheck("error_codes").check(tmp_path, ["x.a", "x.b", "x.c"]) == ["x.a", "x.b"]


def _run(*args: str, stdin: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "flowchecks.error_codes", *args],
        cwd=REPO_ROOT,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_F4_r1_cli_reads_codes_from_a_file_or_stdin_and_exits_by_result(tmp_path) -> None:
    translations = tmp_path / "translations"
    _write(translations, "en", {"x.a": "A", "x.b": "B"})
    _write(translations, "vi", {"x.a": "A"})
    codes = tmp_path / "codes.txt"
    codes.write_text("x.a\n\n x.b \n", encoding="utf-8")

    from_file = _run("--translations", str(translations), "--codes", str(codes))
    from_stdin = _run("--translations", str(translations), "--codes", "-", stdin="x.a\n")
    unreadable = _run("--translations", str(tmp_path / "none"), "--codes", str(codes))

    assert from_file.returncode == 1 and "x.b" in from_file.stdout and "x.a" not in from_file.stdout
    assert from_stdin.returncode == 0, from_stdin.stdout + from_stdin.stderr
    assert unreadable.returncode == 2 and "none" in unreadable.stderr
