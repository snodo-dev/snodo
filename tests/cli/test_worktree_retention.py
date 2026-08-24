"""Tests for worktree retention on non-completion.

FILE: tests/cli/test_worktree_retention.py

A failed task must keep its worktree; a completed task tears it down. These
tests drive ``_execute_task`` against a real git repo + real worktree with the
closure outcome mocked.
"""

import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

import pytest

from snodo.compiler.models import Protocol, Mode, Validator, DisagreementPolicy
from snodo.core.interfaces import Task
from snodo.engine.closure import ClosureNode


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


def _protocol():
    return Protocol(
        protocol_id="t", name="T", version="1.0.0",
        modes=[Mode(mode_id="producer", name="Producer", tools=["edit"], validators=["v1"])],
        validators=[Validator(validator_id="v1", validator_type="security")],
        disagreement_policy=DisagreementPolicy.UNANIMOUS,
        initial_mode="producer",
    )


def _task():
    return Task(id="task_abc123", spec="do the thing")


def _wt_dir(project_root):
    return Path(project_root).parent / ".snodo-worktrees" / "task_abc123"


def _run(project_root, outcome, retain=False):
    from snodo.cli.commands.run_cmd import _execute_task

    protocol = _protocol()
    task = _task()
    args = SimpleNamespace(
        mock=True, verbose=False, audit_log=None, session_manager=None,
        resume=None, retain_worktree=retain,
    )
    tree = ClosureNode(task_id=task.id, depth=0, outcome=outcome)
    final_state = {"is_blocked": outcome != "resolved", "artifacts": []}

    with patch("snodo.infrastructure.paths.require_project_root", return_value=str(project_root)):
        with patch("snodo.cli.commands.run_cmd._resolve_session", return_value=(None, "producer")):
            with patch("snodo.cli.commands.run_cmd._setup_memory", return_value=(None, None, None)):
                with patch("snodo.cli.commands.run_cmd._build_graph", return_value=MagicMock()):
                    with patch("snodo.engine.closure.run_to_closure",
                               return_value=(final_state, tree)):
                        return _execute_task(args, protocol, task, "gpt-4")


class TestWorktreeRetention:
    def test_blocked_task_keeps_worktree_and_names_it(self, git_project, capsys):
        result = _run(git_project, "blocked")
        assert result == 1
        assert _wt_dir(git_project).exists()
        out = capsys.readouterr().out
        assert "Worktree preserved for inspection" in out
        assert str(_wt_dir(git_project)) in out

    def test_internal_error_keeps_worktree(self, git_project):
        _run(git_project, "internal_error")
        assert _wt_dir(git_project).exists()

    def test_completed_task_removes_worktree(self, git_project):
        result = _run(git_project, "resolved")
        assert result == 0
        assert not _wt_dir(git_project).exists()

    def test_retain_flag_keeps_worktree_on_success(self, git_project):
        _run(git_project, "resolved", retain=True)
        assert _wt_dir(git_project).exists()
