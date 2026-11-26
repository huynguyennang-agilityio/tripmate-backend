#!/usr/bin/env python3
"""PreToolUse hook for Bash: block the commands CLAUDE.md forbids (exit 2 blocks, 0 allows).

Rules (each block message names its rule):

- human-approved: only the product owner adds the label `human-approved`.
- no-push-to-main: nothing is pushed to `main` (explicitly, or from `main` with no refspec).
- no-force-push: no force push in any form (`--force`, `-f`, `--force-with-lease`, `+refspec`).
- no-bare-stash: `git stash` and `git stash pop` touch a stack shared by every session.
- no-verify: hooks and checks are never skipped (`--no-verify`, `git commit -n`).

The command is split into simple commands (`&&`, `||`, `;`, `|`, `&`, newlines, `$(...)`,
backticks, `bash -c "..."`), so `cd x && git push origin main` is caught. Known limit: a command
built from a variable or a file (`git push origin "$BRANCH"`) cannot be read.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

SEPARATORS = frozenset({"&&", "||", ";", "|", "&", "\n", "(", ")", "{", "}"})
WRAPPERS = frozenset(
    {"sudo", "command", "env", "time", "nohup", "exec", "builtin", "!", "then", "do", "else"}
)
SHELLS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
GIT_GLOBAL_WITH_VALUE = frozenset({"-C", "-c", "--git-dir", "--work-tree", "--namespace"})
PUSH_WITH_VALUE = frozenset({"-o", "--push-option", "--repo", "--receive-pack", "--exec"})
MAIN_BRANCHES = frozenset({"main"})
LABEL = "human-approved"


class Block(Exception):  # noqa: N818 - a signal, not an error
    """Raised with the message shown to Claude."""


def _skip_group(text: str, i: int, close: str) -> int:
    """Return the index after the group that starts at `i` (after its opener)."""
    depth = 1
    opener = "(" if close == ")" else ""
    n = len(text)
    while i < n and depth:
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c in "'\"":
            end = text.find(c, i + 1)
            i = n if end < 0 else end + 1
            continue
        if opener and c == opener:
            depth += 1
        elif c == close:
            depth -= 1
        i += 1
    return i


def tokenize(text: str) -> tuple[list[list[str]], list[str]]:
    """Split a shell line into simple commands (lists of words) and substitution bodies."""
    commands: list[list[str]] = []
    current: list[str] = []
    subs: list[str] = []
    pending_heredocs: list[str] = []
    word: list[str] = []
    in_word = False
    i, n = 0, len(text)

    def end_word() -> None:
        nonlocal in_word
        if in_word:
            current.append("".join(word))
            word.clear()
            in_word = False

    def end_command() -> None:
        nonlocal current
        end_word()
        if current:
            commands.append(current)
        current = []

    while i < n:
        c = text[i]
        if c in " \t\r":
            end_word()
            i += 1
        elif c == "\n":
            end_command()
            i += 1
            while pending_heredocs:
                delimiter = pending_heredocs.pop(0)
                while i < n:
                    end = text.find("\n", i)
                    line = text[i : n if end < 0 else end]
                    i = n if end < 0 else end + 1
                    if line.strip() == delimiter:
                        break
        elif c == "\\":
            if i + 1 < n and text[i + 1] != "\n":
                word.append(text[i + 1])
                in_word = True
            i += 2
        elif c == "'":
            end = text.find("'", i + 1)
            end = n if end < 0 else end
            word.append(text[i + 1 : end])
            in_word = True
            i = end + 1
        elif c == '"':
            in_word = True
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\" and i + 1 < n:
                    word.append(text[i + 1])
                    i += 2
                elif text.startswith("$(", i):
                    end = _skip_group(text, i + 2, ")")
                    body = text[i + 2 : end - 1]
                    if "<<" not in body:
                        subs.append(body)
                    word.append(text[i:end])
                    i = end
                else:
                    word.append(text[i])
                    i += 1
            i += 1
        elif text.startswith("$(", i):
            end = _skip_group(text, i + 2, ")")
            body = text[i + 2 : end - 1]
            if "<<" not in body:
                subs.append(body)
            word.append(text[i:end])
            in_word = True
            i = end
        elif c == "`":
            end = text.find("`", i + 1)
            end = n if end < 0 else end
            subs.append(text[i + 1 : end])
            word.append(text[i : end + 1])
            in_word = True
            i = end + 1
        elif c in "<>":
            # A redirection: drop a file descriptor number just before it, then its target.
            if in_word and "".join(word).isdigit():
                word.clear()
                in_word = False
            end_word()
            j = i
            while j < n and text[j] in "<>&|":
                j += 1
            operator = text[i:j]
            while j < n and text[j] in " \t":
                j += 1
            k = j
            while k < n and text[k] not in " \t\r\n;&|()":
                if text[k] in "'\"":
                    close = text.find(text[k], k + 1)
                    k = n if close < 0 else close + 1
                else:
                    k += 1
            target = text[j:k].strip("'\"")
            if operator == "<<" or operator == "<<-":
                pending_heredocs.append(target)
            i = k
        elif c in ";|&()":
            end_command()
            j = i + 1
            if c in "|&" and j < n and text[j] == c:
                j += 1
            i = j
        else:
            word.append(c)
            in_word = True
            i += 1
    end_command()
    return commands, subs


def _git_subcommand(args: list[str]) -> tuple[list[str], str | None, list[str], str | None]:
    """Return (global options, subcommand, its arguments, the -C directory)."""
    i = 0
    directory: str | None = None
    while i < len(args):
        arg = args[i]
        if arg in GIT_GLOBAL_WITH_VALUE:
            if arg == "-C" and i + 1 < len(args):
                directory = args[i + 1]
            i += 2
        elif arg.startswith("-"):
            i += 1
        else:
            return args[:i], arg, args[i + 1 :], directory
    return args, None, [], directory


def _short_flags(arg: str, value_flags: str) -> str:
    """The letters of a short-option cluster (`-fu`), up to a flag that takes a value."""
    letters = ""
    for ch in arg[1:]:
        letters += ch
        if ch in value_flags:
            break
    return letters


def _current_branch(cwd: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],  # noqa: S607
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def _check_push(args: list[str], cwd: str) -> None:
    positionals: list[str] = []
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--":
            positionals.extend(args[i + 1 :])
            break
        if arg in PUSH_WITH_VALUE:
            i += 2
            continue
        if arg.startswith("--"):
            name = arg.split("=", 1)[0]
            if name in {"--force", "--force-with-lease", "--force-if-includes"}:
                raise Block(
                    f"Rule no-force-push: `{arg}` is blocked. Never force-push; "
                    "ask the product owner to run it themselves."
                )
            if name in {"--all", "--mirror"}:
                raise Block(
                    f"Rule no-push-to-main: `{arg}` would push main too. Push the branch by name."
                )
            if name == "--no-verify":
                raise Block(_no_verify_message())
        elif arg.startswith("-") and len(arg) > 1:
            if "f" in _short_flags(arg, "o"):
                raise Block(
                    f"Rule no-force-push: `{arg}` is a force push. "
                    "Ask the product owner to run it themselves."
                )
        else:
            positionals.append(arg)
        i += 1
    refspecs = positionals[1:]
    if any(spec.startswith("+") for spec in refspecs):
        raise Block(
            "Rule no-force-push: a `+refspec` forces the update. "
            "Ask the product owner to run it themselves."
        )
    if not refspecs or any(spec.lstrip("+") == "HEAD" for spec in refspecs):
        branch = _current_branch(cwd)
        if branch in MAIN_BRANCHES:
            raise Block(
                "Rule no-push-to-main: you are on main and the push has no other target. "
                "Work on a branch and open a pull request."
            )
    for spec in refspecs:
        destination = spec.lstrip("+").rsplit(":", 1)[-1]
        destination = destination.removeprefix("refs/heads/")
        if destination in MAIN_BRANCHES:
            raise Block(
                "Rule no-push-to-main: nothing is pushed to main. "
                "Push a branch and open a pull request; the product owner merges."
            )


def _no_verify_message() -> str:
    return (
        "Rule no-verify: `--no-verify` skips the hooks and checks. "
        "Fix the failure instead; the product owner can run it themselves if it is deliberate."
    )


def _check_git(words: list[str], cwd: str) -> str:
    """Check one git command; return the directory later commands should assume."""
    _, sub, args, directory = _git_subcommand(words[1:])
    if directory:
        cwd = os.path.join(cwd, directory)
    if "--no-verify" in words:
        raise Block(_no_verify_message())
    if sub == "commit" and any(
        a.startswith("-") and not a.startswith("--") and "n" in _short_flags(a, "mFcCtS")
        for a in args
    ):
        raise Block(_no_verify_message())
    if sub == "push":
        _check_push(args, cwd)
    elif sub == "stash":
        stash_sub = args[0] if args else None
        if stash_sub is None or stash_sub.startswith("-") or stash_sub in {"pop", "save"}:
            raise Block(
                "Rule no-bare-stash: `git stash` and `git stash pop` use a stack shared with "
                "other sessions. Commit a WIP instead, or use "
                "`git stash push -m <tag>` and `git stash apply <sha>`."
            )
    return cwd


def _parts(token: str) -> list[str]:
    return [p.strip("[]\"' ") for p in re.split(r"[,=/]", token)]


def _human_approved_message() -> str:
    return (
        "Rule human-approved: only the product owner adds the label `human-approved`. "
        "Claude Code never adds it."
    )


def _added_labels(words: list[str]) -> list[str]:
    """The values of `--add-label`, `--label`, and `-l` in a `gh pr` or `gh issue` command."""
    values: list[str] = []
    for i, word in enumerate(words):
        if word in {"--add-label", "--label", "-l"} and i + 1 < len(words):
            values.append(words[i + 1])
        elif word.startswith(("--add-label=", "--label=")):
            values.append(word.split("=", 1)[1])
        elif word.startswith("-l") and not word.startswith("--") and len(word) > 2:
            values.append(word[2:])
    return values


def _check_gh(words: list[str]) -> None:
    if not any(LABEL in _parts(t) for t in words[1:]):
        return
    lowered = [w.lower() for w in words]
    if words[1:2] == ["api"]:
        method = ""
        for i, word in enumerate(lowered):
            if word in {"-x", "--method"} and i + 1 < len(lowered):
                method = lowered[i + 1]
            elif word.startswith("--method="):
                method = word.split("=", 1)[1]
        if method != "delete":
            raise Block(_human_approved_message())
    elif words[1:2] == ["label"]:
        if words[2:3] not in (["delete"], ["list"]):
            raise Block(_human_approved_message())
    elif words[2:3] in (["create"], ["edit"]) and any(
        LABEL in _parts(value) for value in _added_labels(words[1:])
    ):
        raise Block(_human_approved_message())


def _program(words: list[str]) -> list[str]:
    """Drop leading `VAR=value` words and wrappers such as `sudo`."""
    i = 0
    while i < len(words) and (
        re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", words[i]) or words[i] in WRAPPERS
    ):
        i += 1
    return words[i:]


def check_command(command: str, cwd: str, depth: int = 0) -> None:
    """Raise Block when any simple command of `command` breaks a rule."""
    if depth > 4:
        return
    commands, subs = tokenize(command)
    for body in subs:
        check_command(body, cwd, depth + 1)
    for raw_words in commands:
        words = _program(raw_words)
        if not words:
            continue
        program = os.path.basename(words[0])
        if program == "cd" and len(words) > 1:
            cwd = os.path.join(cwd, os.path.expanduser(words[1]))
        elif program == "git":
            cwd = _check_git(words, cwd)
        elif program == "gh":
            _check_gh(words)
        elif program in {"curl", "wget", "http", "https"}:
            if LABEL in command and "labels" in command and "-X DELETE" not in command:
                raise Block(_human_approved_message())
        elif program in SHELLS and "-c" in words[1:]:
            index = words.index("-c")
            if index + 1 < len(words):
                check_command(words[index + 1], cwd, depth + 1)
        elif program == "eval" and len(words) > 1:
            check_command(" ".join(words[1:]), cwd, depth + 1)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return 0
    cwd = payload.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    try:
        check_command(command, cwd)
    except Block as block:
        print(f"Blocked by .claude/hooks/bash_guard.py. {block}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
