"""Tests for the merge command's CI gate (Fixes #56).

A merge must not happen on an unverified branch, and an unverified branch must
be visibly different from a green one.  The merge command refuses to merge
when CI has not run, is in progress, or has failed; only a green conclusion
(or an explicit --force) reaches the merge.
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from snodo.cli.commands.merge_cmd import merge_command


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    (tmp_path / ".snodo").mkdir()
    return tmp_path


def _conclusion(state, detail="", run_id=None):
    return SimpleNamespace(state=state, detail=detail, run_id=run_id)


class TestMergeCommandCIGate:
    def test_refuses_when_ci_not_run(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        with patch("snodo.cli.commands.merge_cmd.require_project_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.branch_ci_conclusion",
                   return_value=_conclusion("not_run", "CI has never run on branch 'agent-a'.")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branch="agent-a", force=False))

        assert rc == 1
        merge.assert_not_called()
        out = capsys.readouterr()
        assert "CI has not run" in out.err
        assert "never run" in out.err

    def test_refuses_when_ci_in_progress(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        with patch("snodo.cli.commands.merge_cmd.require_project_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.branch_ci_conclusion",
                   return_value=_conclusion("in_progress", "CI run 9 is queued")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branch="agent-a", force=False))

        assert rc == 1
        merge.assert_not_called()
        assert "CI in progress" in capsys.readouterr().err

    def test_refuses_when_ci_failed(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        with patch("snodo.cli.commands.merge_cmd.require_project_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.branch_ci_conclusion",
                   return_value=_conclusion("fail", "CI run 7 concluded 'failure'")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branch="agent-a", force=False))

        assert rc == 1
        merge.assert_not_called()
        assert "CI failed" in capsys.readouterr().err

    def test_merges_when_ci_green(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        with patch("snodo.cli.commands.merge_cmd.require_project_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.branch_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged") as merge:
            rc = merge_command(SimpleNamespace(branch="agent-a", force=False))

        assert rc == 0
        merge.assert_called_once_with(str(repo), "agent-a")
        assert "CI green" in capsys.readouterr().out

    def test_force_bypasses_gate(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        with patch("snodo.cli.commands.merge_cmd.require_project_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.branch_ci_conclusion") as gate, \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged") as merge:
            rc = merge_command(SimpleNamespace(branch="agent-a", force=True))

        assert rc == 0
        gate.assert_not_called()
        merge.assert_called_once_with(str(repo), "agent-a")
        assert "--force" in capsys.readouterr().err

    def test_ci_gate_error_refuses(self, tmp_path, capsys):
        from snodo.infrastructure.ci_gate import CIGateError

        repo = _init_repo(tmp_path)
        with patch("snodo.cli.commands.merge_cmd.require_project_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.branch_ci_conclusion",
                   side_effect=CIGateError("gh not installed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branch="agent-a", force=False))

        assert rc == 1
        merge.assert_not_called()
        assert "gh not installed" in capsys.readouterr().err

    def test_conflict_escalates(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        with patch("snodo.cli.commands.merge_cmd.require_project_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.branch_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="conflict"):
            rc = merge_command(SimpleNamespace(branch="agent-a", force=False))

        assert rc == 2
        assert "Merge conflict" in capsys.readouterr().err
