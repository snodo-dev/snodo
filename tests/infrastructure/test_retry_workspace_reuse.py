"""Retries reuse repository-owned task workspaces without losing prior work."""

import subprocess

import pytest

from snodo.infrastructure import worktree


def _init_repo(root):
    root.mkdir()
    subprocess.run(["git", "init", "-qb", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.org"], cwd=root, check=True)
    (root / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=root, check=True)


def _leave_work(path):
    (path / "retry-work.txt").write_text("preserved\n")


def test_retry_reuses_registered_task_workspace_with_uncommitted_work(tmp_path, monkeypatch):
    monkeypatch.setattr(worktree, "_prepare_task_environment", lambda *args: None)
    root = tmp_path / "project"
    _init_repo(root)

    first = worktree.create_worktree(str(root), "task_retry", "Add feature")
    _leave_work(first)

    second = worktree.create_worktree(str(root), "task_retry", "Add feature")

    assert second == first
    assert (second / "retry-work.txt").read_text() == "preserved\n"


def test_retry_reuses_registered_legacy_workspace_for_plan_task(tmp_path, monkeypatch):
    monkeypatch.setattr(worktree, "_prepare_task_environment", lambda *args: None)
    root = tmp_path / "project"
    _init_repo(root)

    legacy = worktree.create_worktree(str(root), "task_legacy", "Add feature")
    _leave_work(legacy)

    reused = worktree.create_worktree(
        str(root), "task_legacy", "Add feature", plan_name="release",
    )

    assert reused == legacy
    assert (reused / "retry-work.txt").read_text() == "preserved\n"


def test_retry_refuses_registered_workspace_on_another_task_branch(tmp_path, monkeypatch):
    monkeypatch.setattr(worktree, "_prepare_task_environment", lambda *args: None)
    root = tmp_path / "project"
    _init_repo(root)
    workspace = worktree.worktree_path(str(root), "task_expected")
    workspace.parent.mkdir(parents=True, exist_ok=True)
    foreign_branch = "task/task_other/add-feature"
    subprocess.run(
        ["git", "worktree", "add", "-qb", foreign_branch, str(workspace)],
        cwd=root, check=True,
    )
    _leave_work(workspace)

    with pytest.raises(worktree.WorktreeIsolationError) as exc_info:
        worktree.create_worktree(str(root), "task_expected", "Add feature")

    message = str(exc_info.value)
    assert foreign_branch in message
    assert "snodo worktree remove task_expected" in message
    assert workspace.exists()
    assert (workspace / "retry-work.txt").read_text() == "preserved\n"
    assert subprocess.check_output(
        ["git", "branch", "--show-current"], cwd=workspace, text=True,
    ).strip() == foreign_branch
