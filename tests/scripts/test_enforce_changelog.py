"""Tests for the branch-scoped changelog ratchet."""

import importlib.util
import subprocess
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "enforce_changelog.py"
_spec = importlib.util.spec_from_file_location("enforce_changelog", SCRIPT_PATH)
changelog_check = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(changelog_check)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path, message: str, changelog: str = "# Changelog\n") -> tuple[Path, str]:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "CHANGELOG.md").write_text(changelog, encoding="utf-8")
    _git(tmp_path, "add", "CHANGELOG.md")
    _git(tmp_path, "commit", "-qm", "baseline")
    base = _git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "change.txt").write_text("change\n", encoding="utf-8")
    _git(tmp_path, "add", "change.txt")
    _git(tmp_path, "commit", "-qm", message)
    return tmp_path, base


def test_issue_closing_commit_without_entry_fails_and_names_issue(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, "fix: close the broken path (Fixes #987)")

    failures = changelog_check.check(repo, base, repo / "CHANGELOG.md")

    assert len(failures) == 1
    assert "issue #987" in failures[0]
    assert "add a changelog entry" in failures[0]


def test_chore_commit_closing_nothing_passes(tmp_path: Path) -> None:
    repo, base = _repo(tmp_path, "chore: tidy the test fixture")

    assert changelog_check.check(repo, base, repo / "CHANGELOG.md") == []
