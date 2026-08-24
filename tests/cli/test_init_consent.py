"""Tests for the `snodo init` trusted-repository consent gate (ADR 014).

FILE: tests/cli/test_init_consent.py

Covers:
- decline path: prompt answered "no" → abort, no files written
- accept path: prompt answered "yes" → proceeds
- --yes / --no-input: skip the prompt entirely
- non-TTY stdin without a flag → fail with guidance, no hang
- .gitignore hygiene: append, create-if-absent, never duplicate
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from snodo.cli.main import main


@pytest.fixture
def temp_project_dir():
    """Create a temporary git repo and chdir into it for the duration."""
    temp_dir = tempfile.mkdtemp()
    subprocess.run(["git", "init", "-q"], cwd=temp_dir, check=False)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=temp_dir, check=False)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=temp_dir, check=False)
    (Path(temp_dir) / "README.md").write_text("test")
    subprocess.run(["git", "add", "README.md"], cwd=temp_dir, check=False)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=temp_dir, check=False)

    original_cwd = Path.cwd()
    os.chdir(temp_dir)
    try:
        yield Path(temp_dir)
    finally:
        os.chdir(original_cwd)
        shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def no_keygen():
    """Prevent init from writing real RS256 keys under ~/.ssh/NO-AGENT."""
    with patch(
        "snodo.infrastructure.signing_keys.keypair_exists", return_value=True
    ), patch(
        "snodo.infrastructure.signing_keys.generate_keypair",
        return_value=("/tmp/snodo.pem", "/tmp/snodo.pub.pem"),
    ):
        yield


# === Consent gate ===

def test_init_declines_when_prompt_answered_no(temp_project_dir, no_keygen, capsys):
    """Declining the consent prompt aborts with no files written."""
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="n"):
        with patch("sys.argv", ["snodo", "init", "--template", "solo"]):
            result = main()

    assert result == 1
    assert not (temp_project_dir / ".snodo").exists()
    assert not (temp_project_dir / ".gitignore").exists()


def test_init_proceeds_when_prompt_answered_yes(temp_project_dir, no_keygen):
    """Answering 'yes' to the consent prompt proceeds normally."""
    with patch("sys.stdin.isatty", return_value=True), \
         patch("builtins.input", return_value="y"):
        with patch("sys.argv", ["snodo", "init", "--template", "solo"]):
            result = main()

    assert result == 0
    assert (temp_project_dir / ".snodo" / "protocol.yml").exists()


def test_init_yes_flag_skips_prompt(temp_project_dir, no_keygen):
    """--yes skips the prompt entirely (input is never called)."""
    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--yes"]):
        with patch("builtins.input", side_effect=AssertionError("input should not be called")):
            result = main()

    assert result == 0
    assert (temp_project_dir / ".snodo").exists()


def test_init_no_input_flag_skips_prompt(temp_project_dir, no_keygen):
    """--no-input is an alias for --yes."""
    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--no-input"]):
        with patch("builtins.input", side_effect=AssertionError("input should not be called")):
            result = main()

    assert result == 0
    assert (temp_project_dir / ".snodo").exists()


def test_init_non_tty_without_flag_fails(temp_project_dir, no_keygen, capsys):
    """Non-TTY stdin without --yes fails with guidance instead of hanging."""
    with patch("sys.stdin.isatty", return_value=False):
        with patch("sys.argv", ["snodo", "init", "--template", "solo"]):
            result = main()

    assert result == 1
    assert not (temp_project_dir / ".snodo").exists()
    err = capsys.readouterr().err
    assert "--yes" in err


def test_init_yes_flag_on_non_tty_succeeds(temp_project_dir, no_keygen):
    """--yes works when stdin is not a terminal (CI/scripted)."""
    with patch("sys.stdin.isatty", return_value=False):
        with patch("sys.argv", ["snodo", "init", "--template", "solo", "--yes"]):
            result = main()

    assert result == 0
    assert (temp_project_dir / ".snodo").exists()


# === .gitignore hygiene ===

def test_init_appends_gitignore_entry(temp_project_dir, no_keygen):
    """A successful init ensures exactly one .snodo/ entry in .gitignore."""
    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--yes"]):
        result = main()
    assert result == 0

    gitignore = temp_project_dir / ".gitignore"
    assert gitignore.exists()
    assert gitignore.read_text().splitlines().count(".snodo/") == 1


def test_init_gitignore_entry_idempotent(temp_project_dir, no_keygen):
    """Running init twice (with --force) does not duplicate the .snodo/ entry."""
    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--yes"]):
        main()
    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--force", "--yes"]):
        result = main()

    assert result == 0
    gitignore = temp_project_dir / ".gitignore"
    assert gitignore.read_text().splitlines().count(".snodo/") == 1


def test_init_respects_existing_gitignore_entry(temp_project_dir, no_keygen):
    """A pre-existing .snodo/ entry is not duplicated."""
    (temp_project_dir / ".gitignore").write_text("node_modules/\n.snodo/\n")

    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--yes"]):
        result = main()

    assert result == 0
    content = (temp_project_dir / ".gitignore").read_text()
    assert content.splitlines().count(".snodo/") == 1
    assert "node_modules/" in content


def test_init_appends_without_duplicate_when_no_trailing_newline(temp_project_dir, no_keygen):
    """Appending to a .gitignore lacking a trailing newline still yields one entry."""
    (temp_project_dir / ".gitignore").write_text("dist/")

    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--yes"]):
        result = main()

    assert result == 0
    content = (temp_project_dir / ".gitignore").read_text()
    assert content.splitlines().count(".snodo/") == 1
    assert "dist/" in content


# === .gitignore durability (git clean protection) ===

def test_init_commits_gitignore(temp_project_dir, no_keygen):
    """A successful init commits .gitignore so it is tracked."""
    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--yes"]):
        result = main()

    assert result == 0
    tracked = subprocess.run(
        ["git", "ls-files", ".gitignore"],
        cwd=temp_project_dir, capture_output=True, text=True, check=True,
    ).stdout
    assert ".gitignore" in tracked


def test_git_clean_does_not_remove_snodo(temp_project_dir, no_keygen):
    """After init, `git clean -fd` (twice) must not remove .snodo/."""
    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--yes"]):
        result = main()
    assert result == 0

    # Two cleans reproduce the failure mode: the first removes an untracked
    # .gitignore, the second removes the now-unignored .snodo/.
    subprocess.run(["git", "clean", "-fd"], cwd=temp_project_dir, check=True)
    subprocess.run(["git", "clean", "-fd"], cwd=temp_project_dir, check=True)

    assert (temp_project_dir / ".snodo").exists()
    assert (temp_project_dir / ".snodo" / "protocol.yml").exists()


def test_init_commits_only_gitignore(temp_project_dir, no_keygen):
    """Committing .gitignore must not sweep unrelated staged changes."""
    # Stage an unrelated file before init.
    (temp_project_dir / "unrelated.txt").write_text("x")
    subprocess.run(["git", "add", "unrelated.txt"], cwd=temp_project_dir, check=True)

    with patch("sys.argv", ["snodo", "init", "--template", "solo", "--yes"]):
        result = main()
    assert result == 0

    # The unrelated file must still be staged (not committed by init).
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=temp_project_dir, capture_output=True, text=True, check=True,
    ).stdout
    assert "unrelated.txt" in status
    # .gitignore must be committed (tracked, not in the working-tree status).
    assert ".gitignore" not in status
