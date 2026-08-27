"""Tests for the merge command's CI gate (Fixes #56, #57).

The merge is authorised by CI, not by an agent's self-reported gate result.
The command refuses to merge when CI has not run, is in progress, or has
failed; only a green conclusion (or an explicit --force) reaches the merge. It
operates on the git root (not a .snodo/ project), merges multiple branches in
scope, skips branches with no new commits (resume-safe), and stops on the first
refusal or conflict.
"""

import subprocess
from types import SimpleNamespace
from unittest.mock import patch

from snodo.cli.commands.merge_cmd import (
    _short_name,
    merge_command,
)


def _init_repo(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "init.defaultBranch", "main"], cwd=tmp_path, check=True)
    (tmp_path / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=tmp_path, check=True)
    # Ensure the current branch is named "main".
    current = subprocess.run(
        ["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=tmp_path,
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    if current != "main":
        subprocess.run(["git", "branch", "-M", "main"], cwd=tmp_path, check=True)
    return tmp_path


def _make_branch(repo, name):
    subprocess.run(["git", "checkout", "-qb", name], cwd=repo, check=True)
    (repo / f"{name}.txt").write_text("feature\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", f"feature {name}"], cwd=repo, check=True)
    subprocess.run(["git", "checkout", "-q", "main"], cwd=repo, check=True)


def _conclusion(state, detail="", run_id=None):
    return SimpleNamespace(state=state, detail=detail, run_id=run_id)


class TestShortName:
    def test_agent_short_name_normalises(self):
        assert _short_name("a") == "agent-a"
        assert _short_name("c") == "agent-c"

    def test_agent_full_name_passes_through(self):
        assert _short_name("agent-a") == "agent-a"

    def test_snodo_prefix_normalises(self):
        assert _short_name("snodo-a") == "agent-a"

    def test_other_branches_pass_through(self):
        assert _short_name("feature-x") == "feature-x"


class TestMergeCommandCIGate:
    def test_refuses_when_ci_not_run(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("not_run", "CI has never run on branch 'agent-a'.")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        merge.assert_not_called()
        out = capsys.readouterr()
        assert "CI has not run" in out.err

    def test_refuses_when_ci_in_progress(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("in_progress", "CI run 9 is queued")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        merge.assert_not_called()
        assert "CI in progress" in capsys.readouterr().err

    def test_refuses_when_ci_failed(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("fail", "CI run 7 concluded 'failure'")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        merge.assert_not_called()
        assert "CI failed" in capsys.readouterr().err

    def test_startup_failure_message_points_at_workflow_not_branch(self, tmp_path, capsys):
        """A run that never started is not the branch's fault (Fixes #74)."""
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("startup_failure", "CI never started")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        merge.assert_not_called()
        out = capsys.readouterr().err
        assert "never started" in out
        assert "workflow" in out

    def test_cancelled_message_does_not_blame_branch(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("cancelled", "CI run 8 was cancelled")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        merge.assert_not_called()
        assert "cancelled" in capsys.readouterr().err

    def test_timed_out_message_does_not_merge_branch(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("timed_out", "CI run 80 timed out")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        merge.assert_not_called()
        assert "timed out" in capsys.readouterr().err

    def test_stale_message_points_at_current_commit(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("stale", "CI run 7 on commit deadbeef")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        merge.assert_not_called()
        assert "stale" in capsys.readouterr().err

    def test_merges_when_ci_green(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 0
        merge.assert_called_once_with(str(repo), "agent-a")
        assert "CI green" in capsys.readouterr().out

    def test_merges_multiple_branches_each_gated(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        _make_branch(repo, "agent-c")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a", "agent-c"], force=False))

        assert rc == 0
        assert merge.call_count == 2
        calls = [c[0][1] for c in merge.call_args_list]
        assert calls == ["agent-a", "agent-c"]
        out = capsys.readouterr()
        assert "CI green" in out.out

    def test_force_bypasses_gate(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion") as gate, \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=True))

        assert rc == 0
        gate.assert_not_called()
        merge.assert_called_once_with(str(repo), "agent-a")
        assert "--force" in capsys.readouterr().err

    def test_ci_gate_error_refuses(self, tmp_path, capsys):
        from snodo.infrastructure.ci_gate import CIGateError

        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   side_effect=CIGateError("gh not installed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch") as merge:
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        merge.assert_not_called()
        assert "gh not installed" in capsys.readouterr().err

    def test_conflict_escalates(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="conflict"):
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        assert "Merge conflict" in capsys.readouterr().err

    def test_wrong_branch_refuses(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="agent-b"):
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 2
        assert "not 'main'" in capsys.readouterr().err
