"""Risk gate: the High items of development-flow.md, section 2.1, need `human-approved`.

The path list is a TOML file with `[[rule]]` tables (`glob`, `level`, optional `reason`).
A path's level is the highest level of the rules that match it; a path no rule matches is High.
Built in, whatever the file says: the path list itself, `.github/workflows/**`, every
`CLAUDE.md`, and `.claude/agents/**` are High (they are checks, workflows, and agent roles).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
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


def _timeline_events(text: str) -> list[dict[str, object]]:
    """Events from `gh api --paginate` (arrays one after another) or `--slurp` (an array of arrays)."""
    decoder = json.JSONDecoder()
    events: list[dict[str, object]] = []
    index = 0
    while index < len(text):
        if text[index].isspace():
            index += 1
            continue
        value, index = decoder.raw_decode(text, index)
        items = value if isinstance(value, list) else [value]
        for item in items:
            events.extend(
                i for i in (item if isinstance(item, list) else [item]) if isinstance(i, dict)
            )
    return events


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}


def check_suites_of(text: str) -> list[dict[str, object]]:
    """Check suites from `gh api --paginate [--slurp]` of `commits/{sha}/check-suites` (pages)."""
    suites: list[dict[str, object]] = []
    for page in _timeline_events(text):
        found = page.get("check_suites")
        if isinstance(found, list):
            suites.extend(i for i in found if isinstance(i, dict))
    return suites


def head_arrival(
    events: Iterable[dict[str, object]],
    check_suites: Iterable[dict[str, object]],
    pr_number: int,
    head_ref: str,
    app_slug: str = "github-actions",
) -> datetime | None:
    """When the head commit reached the pull request, by times GitHub sets (never commit dates).

    The earliest check suite of the head commit, made by `app_slug`, for this pull request (it
    lists the pull request, or its branch is the pull request's head branch) is created when the
    push arrives; a suite of the same commit on another branch is not counted, since the commit
    may have been pushed there long before. A force push of the head branch (`head_ref_force_pushed`
    timeline events, by `created_at`) also moves the arrival to its time, whichever is later.
    None when no such suite can be read: the caller treats that as not fresh.
    """
    arrivals: list[datetime] = []
    for suite in check_suites:
        if _as_dict(suite.get("app")).get("slug") != app_slug:
            continue
        pulls = suite.get("pull_requests")
        listed = isinstance(pulls, list) and any(
            _as_dict(p).get("number") == pr_number for p in pulls
        )
        if listed or suite.get("head_branch") == head_ref:
            moment = _time(suite.get("created_at"))
            if moment is not None:
                arrivals.append(moment)
    if not arrivals:
        return None
    arrival = min(arrivals)
    for event in events:
        if event.get("event") == "head_ref_force_pushed":
            pushed = _time(event.get("created_at"))
            if pushed is None:
                return None
            arrival = max(arrival, pushed)
    return arrival


def approval_is_fresh(
    events: Iterable[dict[str, object]],
    check_suites: Iterable[dict[str, object]],
    pr_number: int,
    head_ref: str,
    label: str = APPROVAL_LABEL,
) -> bool:
    """Whether `label` was last added after the head commit reached the pull request.

    `events` are the pull request's timeline events (`GET /repos/{repo}/issues/{n}/timeline`) in
    time order; the label counts when its last `labeled` event (an `unlabeled` event clears it) is
    later than `head_arrival`. A label never added or removed, an arrival that cannot be read, or
    an unreadable time is not fresh. Commit dates are never used: a commit made before the label
    but pushed after it must not look old, and a future-dated one must not block approval.
    """
    event_list = list(events)
    labeled_at: datetime | None = None
    for event in event_list:
        kind = event.get("event")
        if kind in ("labeled", "unlabeled") and _as_dict(event.get("label")).get("name") == label:
            labeled_at = _time(event.get("created_at")) if kind == "labeled" else None
    arrival = head_arrival(event_list, check_suites, pr_number, head_ref)
    return labeled_at is not None and arrival is not None and labeled_at > arrival


def _time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


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
    parser.add_argument(
        "--timeline-file",
        help=f"the pull request's timeline events (JSON); `{APPROVAL_LABEL}` counts only when "
        "it was last added after the head commit reached the pull request",
    )
    parser.add_argument("--check-suites-file", help="check suites of the head commit (JSON)")
    parser.add_argument("--pr-number", type=int, help="the pull request's number")
    parser.add_argument("--head-ref", help="the pull request's head branch")
    args = parser.parse_args(argv)
    if args.timeline_file and not (args.check_suites_file and args.pr_number and args.head_ref):
        parser.error("--timeline-file needs --check-suites-file, --pr-number, and --head-ref")
    labels: list[str] = list(args.label)
    if args.timeline_file and APPROVAL_LABEL in labels:
        events = _timeline_events(Path(args.timeline_file).read_text(encoding="utf-8"))
        suites = check_suites_of(Path(args.check_suites_file).read_text(encoding="utf-8"))
        if not approval_is_fresh(events, suites, args.pr_number, args.head_ref):
            labels.remove(APPROVAL_LABEL)
            print(
                f"Risk gate: `{APPROVAL_LABEL}` was not added after the head commit reached the "
                "pull request (or that time cannot be read); it does not count.",
                file=sys.stderr,
            )
    rules = load_rules(resolve_rules_path(args.rules, args.repo), args.repo)
    needing = check(changed_paths(args.repo, args.base, args.head), rules, labels)
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
