"""Risk gate: the High items of development-flow.md, section 2.1, need `human-approved`.

The path list is a TOML file with `[[rule]]` tables (`glob`, `level`, optional `reason`).
A path's level is the highest level of the rules that match it; a path no rule matches is High.
Built in, whatever the file says: the path list itself, `.github/workflows/**`, every
`CLAUDE.md`, and `.claude/agents/**` are High (they are checks, workflows, and agent roles).
"""

from __future__ import annotations

import argparse
import os
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from flowchecks import _git

APPROVAL_LABEL = "human-approved"
LEVELS = {"Low": 0, "Medium": 1, "High": 2}

# High whatever a caller's path list says (spec 2.12). The path list itself is added by
# `load_rules`, which knows where it is.
BUILTIN_HIGH_GLOBS = (".github/workflows/**", "**/CLAUDE.md", ".claude/agents/**")


@dataclass(frozen=True)
class Rule:
    glob: str
    level: str


def _normal(path: str) -> str:
    path = path.replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path


def _own_globs(path: Path, repo: str | os.PathLike[str] | None) -> list[str]:
    """The glob that names the path list itself, from the repository root."""
    root = Path(repo if repo is not None else Path.cwd()).resolve()
    try:
        return [path.resolve().relative_to(root).as_posix()]
    except ValueError:
        # Outside the repository (a copy made for a run): any file of that name is High.
        return [f"**/{path.name}"]


def load_rules(
    path: str | os.PathLike[str], repo: str | os.PathLike[str] | None = None
) -> list[Rule]:
    """Read a path list. `repo` is the repository root, to name the path list itself.

    Raises ValueError for a file that is not a valid path list: the gate fails closed.
    """
    file = Path(path)
    try:
        data = tomllib.loads(file.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise ValueError(f"cannot read the path list {file}: {error}") from error
    entries = data.get("rule", [])
    if not isinstance(entries, list) or not entries:
        raise ValueError(f"the path list {file} has no [[rule]] table")
    rules: list[Rule] = []
    for number, entry in enumerate(entries, start=1):
        glob, level = entry.get("glob"), entry.get("level")
        if not isinstance(glob, str) or not glob:
            raise ValueError(f"rule {number} of {file} has no glob")
        if level not in LEVELS:
            raise ValueError(f"rule {number} of {file} has level {level!r}, not High/Medium/Low")
        rules.append(Rule(glob=glob, level=level))
    builtin = [*BUILTIN_HIGH_GLOBS, *_own_globs(file, repo)]
    return [*rules, *(Rule(glob=g, level="High") for g in builtin)]


def _matching(path: str, rules: Iterable[Rule]) -> list[Rule]:
    pure = PurePosixPath(_normal(path))
    return [r for r in rules if pure.full_match(r.glob)]


def level_of(path: str, rules: Iterable[Rule]) -> str:
    """ "High", "Medium" or "Low": the highest level of the matching rules; High when none match."""
    matching = _matching(path, rules)
    if not matching:
        return "High"
    return max((r.level for r in matching), key=lambda level: LEVELS[level])


def is_rule_file(path: str, rules: Iterable[Rule]) -> bool:
    """A path is a rule file when any Medium rule matches it, whatever else matches."""
    return any(r.level == "Medium" for r in _matching(path, rules))


def check(changed: Iterable[str], rules: Iterable[Rule], labels: Iterable[str]) -> list[str]:
    """The changed paths that are High and have no `human-approved` label. Empty is a pass."""
    if APPROVAL_LABEL in set(labels):
        return []
    rule_list = list(rules)
    return sorted({p for p in changed if level_of(p, rule_list) == "High"})


def changed_paths(repo: str | os.PathLike[str], base: str, head: str) -> list[str]:
    """The files that differ between `base` and `head` (`git diff --name-only base...head`)."""
    out = _git.git(repo, "diff", "--name-only", "--no-renames", "-z", f"{base}...{head}")
    return [p for p in out.decode("utf-8", "replace").split("\0") if p]


def resolve_rules_path(rules: str, repo: str) -> Path:
    """A relative path list is relative to the repository under check."""
    path = Path(rules)
    return path if path.is_absolute() else Path(repo) / path


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--rules", required=True, help="the path list; relative to --repo")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--label", action="append", default=[], help="a label of the pull request")
    args = parser.parse_args(argv)
    rules = load_rules(resolve_rules_path(args.rules, args.repo), args.repo)
    needing = check(changed_paths(args.repo, args.base, args.head), rules, args.label)
    if needing:
        print(
            f"Risk gate: these changed paths are High and the pull request has no "
            f"`{APPROVAL_LABEL}` label:",
            file=sys.stderr,
        )
        for path in needing:
            print(path)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
