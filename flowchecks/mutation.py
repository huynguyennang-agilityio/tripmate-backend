"""Mutation: a surviving mutant of a changed rule file fails unless the pull request lists it.

The rule files are the paths a `Medium` rule of the path list matches. Mutation testing runs on
the changed ones only. A survivor is listed when the pull request description has a
`## Surviving mutants` section (up to the next `## ` heading) with a line
`- <mutant name>: <reason>`, the reason not empty.

The default runner uses mutmut (3.x), run from the repository root. Its settings
(`[tool.mutmut]` in `pyproject.toml`: `source_paths`, the tests to run) belong to the repository.
"""

from __future__ import annotations

import argparse
import ast
import fnmatch
import os
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path

from flowchecks import _git, risk_gate

SECTION_HEADING = "## Surviving mutants"
# Mutmut statuses that mean the tests did not catch the mutant ("no tests": no test reaches it).
# "not checked" and an interrupted run are failures too: a mutant nobody judged is not a pass.
SURVIVING_STATUSES = frozenset(
    {"survived", "no tests", "suspicious", "not checked", "check was interrupted by user"}
)
# A pragma that takes mutants out of mutmut's reach (or lines out of coverage). Adding one to a
# changed rule file removes survivors from view, so it has to be listed like a survivor.
PRAGMA = re.compile(r"#\s*pragma:\s*no\s+(mutate|cover)", re.IGNORECASE)
_NOTHING_TO_MUTATE = "nothing matches"

Runner = Callable[[Sequence[str]], Iterable[str]]
Mutmut = Callable[..., "subprocess.CompletedProcess[str]"]
AddedLines = Callable[[str], Iterable[str]]


def listed_mutants(description: str) -> set[str]:
    """The mutants the description lists with a reason, in its `## Surviving mutants` section."""
    listed: set[str] = set()
    inside = False
    for line in description.splitlines():
        stripped = line.strip()
        if line.startswith("## "):
            inside = stripped.lower() == SECTION_HEADING.lower()
            continue
        if not inside:
            continue
        match = re.match(r"^[-*]\s+`?([^\s`:]+)`?\s*:\s*(\S.*)$", stripped)
        if match:
            listed.add(match.group(1))
    return listed


def check(
    changed: Iterable[str],
    rules: Iterable[risk_gate.Rule],
    pr_description: str,
    run_mutants: Runner,
    added_lines: AddedLines | None = None,
) -> list[str]:
    """The surviving mutants of the changed rule files that the description does not list.

    With `added_lines` (path -> the lines the change adds to it), a rule file that gains a
    `# pragma: no mutate` or `no cover` is shown as `<path>#pragma`, which the description lists
    like a mutant: `- <path>#pragma: <reason>`.
    """
    rule_list = list(rules)
    rule_files = sorted(
        {p for p in changed if p.endswith(".py") and risk_gate.is_rule_file(p, rule_list)}
    )
    if not rule_files:
        return []
    survivors = set(run_mutants(rule_files))
    if added_lines is not None:
        survivors |= {
            f"{path}#pragma"
            for path in rule_files
            if any(PRAGMA.search(line) for line in added_lines(path))
        }
    return sorted(survivors - listed_mutants(pr_description))


def _module_patterns(rule_files: Sequence[str]) -> list[str]:
    """mutmut names a mutant `<dotted module>.<function>__mutmut_<n>`."""
    patterns: list[str] = []
    for path in rule_files:
        module = path.removesuffix(".py").removesuffix("/__init__").replace("/", ".")
        patterns.append(f"{module}.*")
    return patterns


def _mutmut(repo: str | os.PathLike[str], *args: str) -> subprocess.CompletedProcess[str]:
    """Run `python -m mutmut <args>` in the repository (replaced by a fake in tests)."""
    return subprocess.run(
        [sys.executable, "-m", "mutmut", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def _has_function(repo: str | os.PathLike[str], path: str) -> bool:
    """Whether the file defines any function or method, so mutmut should have mutants for it."""
    try:
        tree = ast.parse((Path(repo) / path).read_text(encoding="utf-8"))
    except (OSError, SyntaxError, ValueError):
        return True  # cannot tell: do not excuse the file
    return any(isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef) for n in ast.walk(tree))


def mutmut_runner(repo: str | os.PathLike[str], mutmut: Mutmut = _mutmut) -> Runner:
    """A runner that runs mutmut on the given rule files and returns the mutants nobody killed."""

    def run_mutants(rule_files: Sequence[str]) -> list[str]:
        patterns = _module_patterns(rule_files)
        ran = mutmut(repo, "run", *patterns)
        log = f"{ran.stdout}\n{ran.stderr}"
        if ran.returncode != 0:
            if _NOTHING_TO_MUTATE in log and not any(_has_function(repo, f) for f in rule_files):
                return []  # none of the files defines a function: there is nothing to mutate
            print(log, file=sys.stderr)
            raise RuntimeError(f"mutmut run failed with exit code {ran.returncode}")
        print(ran.stdout, file=sys.stderr)
        report = mutmut(repo, "results")
        if report.returncode != 0:
            print(report.stderr, file=sys.stderr)
            raise RuntimeError(f"mutmut results failed with exit code {report.returncode}")
        survivors: list[str] = []
        for line in report.stdout.splitlines():
            name, _, status = line.strip().partition(": ")
            if status.strip() in SURVIVING_STATUSES and any(
                fnmatch.fnmatchcase(name, p) for p in patterns
            ):
                survivors.append(name)
        return survivors

    return run_mutants


def added_lines_of(repo: str, base: str, head: str) -> AddedLines:
    """A function from a path to the lines `base...head` adds to it."""

    def added(path: str) -> list[str]:
        diff = _git.git(repo, "diff", "-U0", "--no-renames", f"{base}...{head}", "--", path)
        return [
            line[1:]
            for line in diff.decode("utf-8", "replace").splitlines()
            if line.startswith("+") and not line.startswith("+++")
        ]

    return added


def changed_rule_candidates(repo: str, base: str, head: str) -> list[str]:
    """Changed paths that still exist at `head` (a deleted file has nothing to mutate)."""
    out = _git.git(
        repo, "diff", "--name-only", "--no-renames", "--diff-filter=d", "-z", f"{base}...{head}"
    )
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", required=True, help="the path list; relative to --repo")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--description-file", required=True, help="the pull request description")
    args = parser.parse_args(argv)
    rules = risk_gate.load_rules(risk_gate.resolve_rules_path(args.rules, args.repo), args.repo)
    description = Path(args.description_file).read_text(encoding="utf-8")
    changed = changed_rule_candidates(args.repo, args.base, args.head)
    shown = check(
        changed,
        rules,
        description,
        mutmut_runner(args.repo),
        added_lines_of(args.repo, args.base, args.head),
    )
    if shown:
        print(
            f"Mutation: these mutants survived and the pull request does not list them under "
            f"`{SECTION_HEADING}` as `- <mutant>: <reason>` (a pragma as `<path>#pragma`):",
            file=sys.stderr,
        )
        for mutant in shown:
            print(mutant)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
