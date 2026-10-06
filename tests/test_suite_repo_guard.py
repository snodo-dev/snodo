"""Regression coverage for the session repository mutation guard."""

import subprocess
from pathlib import Path

from tests.conftest import _branch_set, _head_state


def _git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()


def _repository(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-b", "main")
    _git(path, "config", "user.name", "Suite test")
    _git(path, "config", "user.email", "suite@example.invalid")
    (path / "tracked").write_text("initial\n")
    _git(path, "add", "tracked")
    _git(path, "commit", "-m", "initial")
    return path


def test_sibling_worktree_branch_does_not_change_guard_snapshot(tmp_path):
    repo = _repository(tmp_path / "repo")
    sibling_path = tmp_path / "sibling"
    before = _branch_set(repo)

    _git(repo, "worktree", "add", "-b", "task/concurrent/job", str(sibling_path))
    assert _branch_set(repo) == before

    _git(repo, "worktree", "remove", str(sibling_path))
    _git(repo, "branch", "-D", "task/concurrent/job")
    assert _branch_set(repo) == before


def test_own_branch_creation_changes_guard_snapshot(tmp_path):
    repo = _repository(tmp_path / "repo")
    before = _branch_set(repo)

    _git(repo, "branch", "suite-guard-test")

    assert _branch_set(repo) == before | {"suite-guard-test"}


def test_own_head_move_changes_guard_state(tmp_path):
    repo = _repository(tmp_path / "repo")
    before = _head_state(repo)
    _git(repo, "checkout", "--detach")

    assert _head_state(repo) != before
