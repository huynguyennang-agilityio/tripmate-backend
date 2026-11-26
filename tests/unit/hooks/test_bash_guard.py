"""The Bash hook blocks what CLAUDE.md forbids and lets everything else through."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.unit.hooks.support import run_hook


def bash(command: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return run_hook("bash_guard.py", "Bash", {"command": command}, cwd=cwd)


def git_repo(path: Path, branch: str) -> Path:
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "-q", "--allow-empty", "-m", "x"],
        check=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@example.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@example.com",
            "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
            "HOME": str(path),
        },
    )
    return path


BLOCKED = [
    ("gh pr edit 12 --add-label human-approved", "human-approved"),
    ("gh pr edit 12 --add-label bug,human-approved", "human-approved"),
    ("gh pr edit 12 --add-label=human-approved", "human-approved"),
    ("gh issue edit 3 --add-label human-approved", "human-approved"),
    ("gh pr create --title t --body b --label human-approved", "human-approved"),
    ("gh pr create -l human-approved", "human-approved"),
    ("gh api repos/o/r/issues/1/labels -f 'labels[]=human-approved'", "human-approved"),
    ("gh api -X POST repos/o/r/issues/1/labels -f labels=human-approved", "human-approved"),
    ("gh label create human-approved", "human-approved"),
    ("cd repo && gh pr edit 1 --add-label human-approved && echo ok", "human-approved"),
    ("git push origin main", "no-push-to-main"),
    ("git push origin HEAD:main", "no-push-to-main"),
    ("git push origin :main", "no-push-to-main"),
    ("git push origin feature:refs/heads/main", "no-push-to-main"),
    ("git push --delete origin main", "no-push-to-main"),
    ("git push --all", "no-push-to-main"),
    ("git -C ../x push origin main", "no-push-to-main"),
    ("cd x && git add . && git commit -m y && git push origin main", "no-push-to-main"),
    ("echo a; git push origin main", "no-push-to-main"),
    ("git status | cat\ngit push origin main", "no-push-to-main"),
    ("bash -c 'git push origin main'", "no-push-to-main"),
    ("echo $(git push origin main)", "no-push-to-main"),
    ("git push --force origin feature", "no-force-push"),
    ("git push -f origin feature", "no-force-push"),
    ("git push -fu origin feature", "no-force-push"),
    ("git push --force-with-lease origin feature", "no-force-push"),
    ("git push --force-with-lease=feature origin feature", "no-force-push"),
    ("git push origin +feature", "no-force-push"),
    ("git push origin +feature:feature", "no-force-push"),
    ("git stash", "no-bare-stash"),
    ("git stash pop", "no-bare-stash"),
    ("git stash -u", "no-bare-stash"),
    ("cd x && git stash && git pull", "no-bare-stash"),
    ("git commit --no-verify -m x", "no-verify"),
    ("git commit -n -m x", "no-verify"),
    ("git commit -nm x", "no-verify"),
    ("git push --no-verify origin feature", "no-verify"),
    ("git merge --no-verify x", "no-verify"),
]


@pytest.mark.parametrize(("command", "rule"), BLOCKED)
def test_blocked_command_exits_2_and_names_the_rule(command: str, rule: str) -> None:
    result = bash(command)
    assert result.returncode == 2, result.stderr
    assert f"Rule {rule}" in result.stderr


ALLOWED = [
    "ls -la",
    "uv run pytest tests/unit -q",
    "gh pr view 12 --json labels",
    "gh pr list --label human-approved",
    "gh pr edit 12 --remove-label human-approved",
    "gh pr edit 12 --add-label bug",
    "gh pr create --title t --body 'a label human-approved is added by the owner' --label bug",
    "gh api -X DELETE repos/o/r/issues/1/labels/human-approved",
    "gh label list",
    "git push origin chore/readme-and-hooks",
    "git push -u origin feature/x",
    "git push origin HEAD:refs/heads/feature/x",
    "git push origin mainline",
    "git stash push -m tag-1",
    "git stash list",
    "git stash apply abc123",
    "git stash drop stash@{0}",
    "git commit -m 'never use --no-verify'",
    "git commit -m 'a note about main and git push origin main'",
    "git commit -m \"$(cat <<'EOF'\nDo not git push origin main\nnor --no-verify\nEOF\n)\"",
    "git commit -am 'fix'",
    "git log --oneline -n 5",
    "git diff main..HEAD",
    "cd x && git status && git log -3",
    "git fetch origin main",
    "git checkout main",
]


@pytest.mark.parametrize("command", ALLOWED)
def test_allowed_command_exits_0(command: str) -> None:
    result = bash(command)
    assert result.returncode == 0, result.stderr


def test_push_with_no_target_is_blocked_on_main(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r", "main")
    for command in ("git push", "git push origin", "git push -u origin HEAD"):
        result = bash(command, cwd=repo)
        assert result.returncode == 2, command
        assert "Rule no-push-to-main" in result.stderr


def test_push_with_no_target_is_allowed_on_a_branch(tmp_path: Path) -> None:
    repo = git_repo(tmp_path / "r", "feature")
    for command in ("git push", "git push origin", "git push -u origin HEAD"):
        assert bash(command, cwd=repo).returncode == 0, command


def test_cd_into_a_repository_on_main_is_followed(tmp_path: Path) -> None:
    git_repo(tmp_path / "r", "main")
    result = bash(f"cd {tmp_path / 'r'} && git push", cwd=tmp_path)
    assert result.returncode == 2
    assert "Rule no-push-to-main" in result.stderr


def test_git_dash_c_directory_is_followed(tmp_path: Path) -> None:
    git_repo(tmp_path / "r", "main")
    result = bash(f"git -C {tmp_path / 'r'} push", cwd=tmp_path)
    assert result.returncode == 2


def test_input_without_a_command_is_allowed() -> None:
    assert run_hook("bash_guard.py", "Bash", {}).returncode == 0
