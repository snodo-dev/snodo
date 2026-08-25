"""Tests for git worktree lifecycle and task-branch merge.

FILE: tests/infrastructure/test_worktree.py
"""

import subprocess
import tempfile
from pathlib import Path

import pytest

from snodo.infrastructure.worktree import (
    WorktreeIsolationError,
    create_worktree,
    delete_task_branch,
    merge_task_branch,
    task_branch_name,
)
from snodo.tools.git import GitError, resolve_base_branch


def _init_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)


def _current_branch(root: Path) -> str:
    return subprocess.run(
        ["git", "branch", "--show-current"], cwd=root,
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _branches(root: Path) -> str:
    return subprocess.run(
        ["git", "branch"], cwd=root, capture_output=True, text=True, check=True,
    ).stdout


@pytest.fixture
def repo():
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        _init_repo(root)
        yield root


# === resolve_base_branch ===

def test_resolve_base_branch_defaults_to_main(repo):
    assert resolve_base_branch(str(repo)) == "main"


def test_resolve_base_branch_uses_remote_head(repo):
    # Simulate a repository whose remote default is not main.
    subprocess.run(["git", "init", "-qb", "master"], cwd=repo, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master"],
        cwd=repo, check=True,
    )
    assert resolve_base_branch(str(repo)) == "master"


# === unborn HEAD (no commits) — fail loud, never degrade to no isolation ===

def _init_unborn_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)


def test_create_worktree_on_unborn_head_raises(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    _init_unborn_repo(root)

    with pytest.raises(WorktreeIsolationError, match="no commits"):
        create_worktree(str(root), "task_1", "do the thing")


def test_setup_for_task_on_unborn_head_propagates(tmp_path):
    """setup_for_task must not swallow the isolation failure and return None.

    Returning None is the silent-degradation path: the caller would run the
    agent in the operator's working tree. The structural error must surface
    so a human can decide (Fixes #29).
    """
    from snodo.infrastructure.worktree import setup_for_task

    root = tmp_path / "proj"
    root.mkdir()
    _init_unborn_repo(root)

    with pytest.raises(WorktreeIsolationError, match="no commits"):
        setup_for_task(str(root), "task_1", "do the thing")


# === create_worktree branches off the resolved base ===

def test_create_worktree_branches_off_non_main_base(repo):
    # Make the default branch "master" (rename main -> master).
    subprocess.run(["git", "branch", "-m", "main", "master"], cwd=repo, check=True)
    subprocess.run(
        ["git", "symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/master"],
        cwd=repo, check=True,
    )
    # Add a commit on master so we can tell the worktree branched from it.
    (repo / "base_only.txt").write_text("base\n")
    subprocess.run(["git", "add", "base_only.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "base commit"], cwd=repo, check=True)

    wt = create_worktree(str(repo), "task_1", "do the thing")

    assert wt.exists()
    assert (wt / "base_only.txt").exists()  # inherited from master, not main


# === merge_task_branch ===

def test_merge_task_branch_success(repo):
    branch = task_branch_name("task_1", "add feature")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    (repo / "feature.txt").write_text("feature\n")
    subprocess.run(["git", "add", "feature.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "feature"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    assert merge_task_branch(str(repo), branch) == "merged"

    assert _current_branch(repo) == "main"
    assert (repo / "feature.txt").exists()  # base branch now has the commit


def test_merge_task_branch_conflict(repo):
    branch = task_branch_name("task_1", "conflicting change")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    (repo / "README.md").write_text("branch content\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "branch change"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("main content\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "main change"], cwd=repo, check=True)

    assert merge_task_branch(str(repo), branch) == "conflict"

    # Base branch is left clean (merge aborted) and the source branch survives.
    assert _current_branch(repo) == "main"
    assert "README.md" not in _current_branch(repo)  # sanity: no conflict markers path
    assert branch in _branches(repo)


def test_merge_task_branch_nonexistent_raises(repo):
    with pytest.raises(GitError):
        merge_task_branch(str(repo), "task/nope/missing")


# === delete_task_branch ===

def test_delete_task_branch(repo):
    branch = task_branch_name("task_1", "delete me")
    subprocess.run(["git", "checkout", "-qb", branch], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)

    delete_task_branch(str(repo), branch)
    assert branch not in _branches(repo)
