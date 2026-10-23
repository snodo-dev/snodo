"""Refuse to reuse workspaces not registered to the requesting project."""

import subprocess
from pathlib import Path

import pytest

from snodo.infrastructure.worktree import (
    WorktreeIsolationError,
    create_worktree,
    worktree_path,
)


def _init_repo(root: Path) -> None:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "tests@example.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "Tests"], cwd=root, check=True)
    (root / "README.md").write_text("initial\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=root, check=True)


def test_refuses_workspace_from_sibling_project_without_touching_it(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir()
    project_a = parent / "project-a"
    project_b = parent / "project-b"
    _init_repo(project_a)
    _init_repo(project_b)

    workspace_a = create_worktree(str(project_a), "task_shared", "Implement shared task")
    marker = workspace_a / "leftover.txt"
    marker.write_text("preserve me\n")

    # Project scoping gives B a distinct path. Put A's existing directory at
    # that path to reproduce the collision while keeping both projects real.
    workspace_b = worktree_path(str(project_b), "task_shared")
    workspace_b.parent.mkdir(parents=True, exist_ok=True)
    workspace_b.symlink_to(workspace_a, target_is_directory=True)

    with pytest.raises(WorktreeIsolationError) as error:
        create_worktree(str(project_b), "task_shared", "Implement shared task")

    message = str(error.value)
    assert str(workspace_b) in message
    assert "branch 'task/task_shared/implement-shared-task'" in message
    assert str(project_b.resolve()) in message
    assert "snodo worktree remove task_shared" in message
    assert marker.read_text() == "preserve me\n"
    assert (workspace_a / ".git").exists()


def test_refuses_existing_plain_directory(tmp_path):
    project = tmp_path / "project"
    _init_repo(project)
    requested_path = worktree_path(str(project), "task_plain")
    requested_path.mkdir(parents=True)
    workspace = worktree_path(str(project), "task_plain")
    workspace.mkdir(parents=True)
    marker = workspace / "keep.txt"
    marker.write_text("not a repository\n")

    with pytest.raises(WorktreeIsolationError) as error:
        create_worktree(str(project), "task_plain", "Implement plain task")

    message = str(error.value)
    assert str(workspace) in message
    assert "not a git repository" in message
    assert "snodo worktree remove task_plain" in message
    assert marker.read_text() == "not a repository\n"


def test_refuses_unregistered_git_repository_and_names_its_branch(tmp_path):
    project = tmp_path / "project"
    _init_repo(project)
    requested_path = worktree_path(str(project), "task_foreign_repo")
    requested_path.mkdir(parents=True)
    workspace = worktree_path(str(project), "task_foreign_repo")
    _init_repo(workspace)

    with pytest.raises(WorktreeIsolationError) as error:
        create_worktree(str(project), "task_foreign_repo", "Implement foreign task")

    message = str(error.value)
    assert str(workspace) in message
    assert "branch 'main'" in message
    assert str(workspace) in message
    assert "snodo worktree remove task_foreign_repo" in message
    assert (workspace / "README.md").read_text() == "initial\n"
