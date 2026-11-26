#!/usr/bin/env python3
"""PreToolUse hook for Edit, Write, MultiEdit, and NotebookEdit: keep secrets out of the repository.

The repository is public, so a secret that is written is a secret that is leaked.
Exit 2 blocks the call; exit 0 allows it.

Blocked paths: `.env` and `.env.*` (but `.env.example` is allowed), `*.pem`, `*.key`,
`id_rsa*`.

Blocked content (the text being written: `content`, `new_string`, `new_source`,
`edits[].new_string`):

- GitHub tokens: `ghp_`, `gho_`, `ghu_`, `ghs_`, `ghr_` (20 or more characters after), `github_pat_`.
- AWS access key IDs: `AKIA` and 16 upper-case letters or digits.
- Private key blocks: `-----BEGIN ... PRIVATE KEY-----`.
- Slack tokens: `xoxa-`, `xoxb-`, `xoxp-` followed by 10 or more token characters.
- A URL with an inline password, `scheme://user:password@host`, unless the password is a
  placeholder: empty, starts with `<`, `$`, `{`, `%`, or `*` (a template, an f-string, a
  redaction), or is one of `password`, `postgres`, `pass`, `changeme`, `example`, `test`
  (the examples of documents and tests). Anything else is treated as a real password.
"""

from __future__ import annotations

import json
import os
import re
import sys

BLOCKED_NAMES = (
    (
        re.compile(r"\.env(\..+)?"),
        "an environment file (`.env`); settings come from the environment",
    ),
    (re.compile(r".+\.pem"), "a `.pem` file"),
    (re.compile(r".+\.key"), "a `.key` file"),
    (re.compile(r"id_rsa.*"), "an SSH key (`id_rsa*`)"),
)
ALLOWED_NAMES = frozenset({".env.example"})

SECRET_PATTERNS = (
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"), "a GitHub token"),
    (re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}"), "a GitHub token"),
    (re.compile(r"\bAKIA[0-9A-Z]{16}\b"), "an AWS access key ID"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key block"),
    (re.compile(r"\bxox[abp]-[0-9A-Za-z-]{10,}"), "a Slack token"),
)
URL_PASSWORD = re.compile(r"\b[a-zA-Z][a-zA-Z0-9+.-]*://[^/\s:@]+:([^/\s@]*)@")
PLACEHOLDER_PASSWORDS = frozenset({"password", "postgres", "pass", "changeme", "example", "test"})


def written_text(tool_input: dict[str, object]) -> list[str]:
    """Every piece of new text in the tool input."""
    texts: list[str] = []
    for key in ("content", "new_string", "new_source"):
        value = tool_input.get(key)
        if isinstance(value, str):
            texts.append(value)
    edits = tool_input.get("edits")
    if isinstance(edits, list):
        for edit in edits:
            if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
                texts.append(edit["new_string"])
    return texts


def path_problem(path: str) -> str | None:
    name = os.path.basename(path)
    if name in ALLOWED_NAMES:
        return None
    for pattern, what in BLOCKED_NAMES:
        if pattern.fullmatch(name):
            return f"Rule no-secret-files: writing {what} is blocked ({name})."
    return None


def content_problem(text: str) -> str | None:
    for pattern, what in SECRET_PATTERNS:
        if pattern.search(text):
            return f"Rule no-secrets: the text looks like {what}."
    for match in URL_PASSWORD.finditer(text):
        password = match.group(1)
        is_template = password.startswith(("<", "$", "{", "%", "*"))
        if password and not is_template and password.lower() not in PLACEHOLDER_PASSWORDS:
            return "Rule no-secrets: the text has a URL with an inline password."
    return None


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        return 0
    problem: str | None = None
    for key in ("file_path", "notebook_path", "path"):
        value = tool_input.get(key)
        if isinstance(value, str):
            problem = problem or path_problem(value)
    for text in written_text(tool_input):
        problem = problem or content_problem(text)
    if problem:
        print(
            f"Blocked by .claude/hooks/file_guard.py. {problem} The repository is public: "
            "put the value in the environment, use a placeholder such as <password>, "
            "or ask the product owner to make this change themselves.",
            file=sys.stderr,
        )
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
