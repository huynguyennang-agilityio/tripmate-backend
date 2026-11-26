"""The Edit and Write hook keeps secret files and secret-looking text out of the repository."""

from __future__ import annotations

import subprocess

import pytest

from tests.unit.hooks.support import run_hook

# Built at run time so that no secret-shaped text is committed in this file.
GITHUB_TOKEN = "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"
GITHUB_PAT = "github_" + "pat_" + "11ABCDEFG0abcdefghij_KLMNOPQRSTuvwxyz0123456789"
AWS_KEY = "AK" + "IA" + "ABCDEFGHIJKLMNOP"
PRIVATE_KEY = "-----BEGIN " + "RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----"
SLACK_TOKEN = "xo" + "xb-" + "1234567890-abcdefghij"


def write(path: str, content: str) -> subprocess.CompletedProcess[str]:
    return run_hook("file_guard.py", "Write", {"file_path": path, "content": content})


@pytest.mark.parametrize(
    "path",
    [
        ".env",
        "/repo/.env",
        ".env.local",
        ".env.production",
        "deploy/key.pem",
        "a/b/server.key",
        "/home/u/.ssh/id_rsa",
        "id_rsa.pub",
        "id_rsa_work",
    ],
)
def test_secret_files_are_blocked(path: str) -> None:
    result = write(path, "x")
    assert result.returncode == 2
    assert "Rule no-secret-files" in result.stderr


@pytest.mark.parametrize(
    "path", [".env.example", "/repo/.env.example", "app/environment.py", "keys.md", "README.md"]
)
def test_other_files_are_allowed(path: str) -> None:
    assert write(path, "DATABASE_URL=\n").returncode == 0


@pytest.mark.parametrize(
    "secret",
    [
        GITHUB_TOKEN,
        GITHUB_PAT,
        AWS_KEY,
        PRIVATE_KEY,
        SLACK_TOKEN,
        "postgresql://tripmate:s3cr3tvalue@db.internal:5432/tripmate",
        "redis://default:hunter2hunter2@cache.example.com:6379/0",
    ],
)
def test_secret_looking_content_is_blocked(secret: str) -> None:
    result = write("app/settings.py", f'VALUE = """{secret}"""\n')
    assert result.returncode == 2
    assert "Rule no-secrets" in result.stderr
    assert secret not in result.stderr


@pytest.mark.parametrize(
    "text",
    [
        "postgresql://user:@host/db",
        "postgresql://user:<password>@host/db",
        "postgresql://user:${DB_PASSWORD}@host/db",
        "postgresql://user:$DB_PASSWORD@host/db",
        "postgresql+asyncpg://tripmate:{password}@127.0.0.1:5432/x",
        "postgresql://postgres:postgres@localhost/db",
        "postgresql://user:password@localhost/db",
        "postgresql+asyncpg://tripmate@127.0.0.1:5432/tripmate_test",
        "https://example.com/path",
        "the prefixes ghp_ and AKIA are mentioned here, and xoxb- too",
        "see https://host:8000/x for details",
    ],
)
def test_placeholders_and_ordinary_text_are_allowed(text: str) -> None:
    assert write("docs/notes.md", text).returncode == 0


def test_edit_new_string_is_checked() -> None:
    result = run_hook(
        "file_guard.py", "Edit", {"file_path": "a.py", "old_string": "x", "new_string": AWS_KEY}
    )
    assert result.returncode == 2


def test_old_string_is_not_checked() -> None:
    result = run_hook(
        "file_guard.py", "Edit", {"file_path": "a.py", "old_string": AWS_KEY, "new_string": "x"}
    )
    assert result.returncode == 0


def test_multiedit_edits_are_checked() -> None:
    edits = [
        {"old_string": "a", "new_string": "b"},
        {"old_string": "c", "new_string": GITHUB_TOKEN},
    ]
    result = run_hook("file_guard.py", "MultiEdit", {"file_path": "a.py", "edits": edits})
    assert result.returncode == 2


def test_notebook_edit_is_checked() -> None:
    result = run_hook(
        "file_guard.py", "NotebookEdit", {"notebook_path": "n.ipynb", "new_source": SLACK_TOKEN}
    )
    assert result.returncode == 2
    assert (
        run_hook("file_guard.py", "NotebookEdit", {"notebook_path": "secret.pem"}).returncode == 2
    )
