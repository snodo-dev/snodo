"""Tests for the snodo worktree list/remove/prune commands.

FILE: tests/cli/test_worktree_cmd.py
"""

import os
import subprocess
import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from snodo.infrastructure.worktree import create_worktree, worktree_path


@pytest.fixture
def git_project(tmp_path):
    # Nest the project so .snodo-worktrees (a sibling of project_root) is unique
    # per test rather than landing in the shared pytest basetemp.
    root = tmp_path / "proj"
    root.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def _set_age(project_root, task_id, days_old):
    p = worktree_path(project_root, task_id)
    mtime = time.time() - days_old * 86400
    os.utime(str(p), (mtime, mtime))


class TestWorktreeCommands:
    def test_list_lists_retained_worktrees(self, git_project, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_list_command

        create_worktree(str(git_project), "task_a", "spec a")
        create_worktree(str(git_project), "task_b", "spec b")

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_list_command(SimpleNamespace())

        assert result == 0
        out = capsys.readouterr().out
        assert "task_a" in out
        assert "task_b" in out
        assert "snodo worktree remove task_a" in out

    def test_remove_deletes_worktree_and_branch(self, git_project):
        from snodo.cli.commands.worktree_cmd import worktree_remove_command
        from snodo.tools.git import GitMCP

        create_worktree(str(git_project), "task_x", "spec x")
        assert worktree_path(git_project, "task_x").exists()
        git = GitMCP(str(git_project))
        assert any(h.name.startswith("task/task_x") for h in git.repo.heads)

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_remove_command(SimpleNamespace(task_id="task_x"))

        assert result == 0
        assert not worktree_path(git_project, "task_x").exists()
        git = GitMCP(str(git_project))
        assert not any(h.name.startswith("task/task_x") for h in git.repo.heads)

    def test_list_filters_hidden_directories(self, git_project, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_list_command
        from snodo.infrastructure.worktree import worktree_dir

        create_worktree(str(git_project), "task_valid", "spec valid")
        hidden_dir = worktree_dir(str(git_project)) / ".snodo-worktrees"
        hidden_dir.mkdir(parents=True, exist_ok=True)

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_list_command(SimpleNamespace())

        assert result == 0
        out = capsys.readouterr().out
        assert "task_valid" in out
        assert ".snodo-worktrees" not in out

    def test_prune_days_zero_refused_unguarded(self, git_project, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_prune_command

        create_worktree(str(git_project), "task_zero", "zero spec")
        assert worktree_path(git_project, "task_zero").exists()

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_prune_command(SimpleNamespace(days=0, force=False))

        assert result == 1
        assert worktree_path(git_project, "task_zero").exists()
        err = capsys.readouterr().err
        assert "must be at least 1 day" in err

    def test_prune_skips_failure_retained_worktrees(self, git_project, capsys, monkeypatch):
        from snodo.cli.commands.worktree_cmd import worktree_prune_command

        create_worktree(str(git_project), "task_failed", "failed spec")
        _set_age(git_project, "task_failed", days_old=30)
        assert worktree_path(git_project, "task_failed").exists()

        monkeypatch.setattr(
            "snodo.cli.commands.worktree_cmd._has_failure_context",
            lambda proj, tid: tid == "task_failed",
        )

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_prune_command(SimpleNamespace(days=7, force=False))

        assert result == 0
        assert worktree_path(git_project, "task_failed").exists()
        out = capsys.readouterr().out
        assert "Skipping task_failed: retained for failure evidence" in out

    def test_prune_removes_stale_and_keeps_fresh(self, git_project):
        from snodo.cli.commands.worktree_cmd import worktree_prune_command

        create_worktree(str(git_project), "task_old", "old spec")
        create_worktree(str(git_project), "task_fresh", "fresh spec")
        _set_age(git_project, "task_old", days_old=30)

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_prune_command(SimpleNamespace(days=7, force=True))

        assert result == 0
        assert not worktree_path(git_project, "task_old").exists()
        assert worktree_path(git_project, "task_fresh").exists()

    def test_list_empty(self, git_project, capsys):
        from snodo.cli.commands.worktree_cmd import worktree_list_command

        with patch("snodo.cli.commands.worktree_cmd.require_project_root",
                   return_value=str(git_project)):
            result = worktree_list_command(SimpleNamespace())

        assert result == 0
        assert "No retained worktrees" in capsys.readouterr().out
