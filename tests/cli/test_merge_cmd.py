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
    _push_branches,
    _short_name,
    ci_wait_command,
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value=("merged", [])) as merge:
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value=("merged", [])) as merge:
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion") as gate, \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value=("merged", [])) as merge:
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
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
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value=("conflict", ["README.md"])):
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 1
        err = capsys.readouterr().err
        assert "Merge conflict" in err
        assert "Conflicting path(s): README.md" in err
        assert "rolled back" in err
        assert "git merge agent-a" in err

    def test_wrong_branch_refuses(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="agent-b"):
            rc = merge_command(SimpleNamespace(branches=["agent-a"], force=False))

        assert rc == 2
        assert "not 'main'" in capsys.readouterr().err


class TestMergeRecordsReview:
    """Merging is where the review verdict is recorded (Fixes #83)."""

    def _merge(self, tmp_path, args, merge_result="merged"):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value=merge_result):
            return merge_command(args)
        return None

    def test_records_merged_and_unreviewed_when_unattended(self, tmp_path, capsys):
        """A merge without a verdict and without a TTY records unreviewed, never
        accepted — an unreviewed merge must not count as accepted."""
        from snodo.infrastructure.audit import AuditLog

        repo = _init_repo(tmp_path)
        audit = AuditLog(str(repo / ".snodo" / "audit.log"))
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged"), \
             patch("snodo.cli.commands.merge_cmd._audit_log", return_value=audit):
            args = SimpleNamespace(branches=["agent-a"], force=False, review=None, no_review=False)
            rc = merge_command(args)

        assert rc == 0
        merged = audit.get_history("task_merged")
        assert len(merged) == 1
        reviews = audit.get_history("human_review_recorded")
        assert len(reviews) == 1
        assert reviews[0].data["verdict"] == "unreviewed"
        err = capsys.readouterr().err
        assert "recorded agent-a as unreviewed" in err

    def test_review_flag_records_verdict(self, tmp_path, capsys):
        from snodo.infrastructure.audit import AuditLog

        repo = _init_repo(tmp_path)
        audit = AuditLog(str(repo / ".snodo" / "audit.log"))
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged"), \
             patch("snodo.cli.commands.merge_cmd._audit_log", return_value=audit):
            args = SimpleNamespace(branches=["agent-a"], force=False, review="accepted", no_review=False)
            rc = merge_command(args)

        assert rc == 0
        reviews = audit.get_history("human_review_recorded")
        assert len(reviews) == 1
        assert reviews[0].data["verdict"] == "accepted"
        assert "recorded review verdict 'accepted'" in capsys.readouterr().out

    def test_invalid_review_flag_records_unreviewed(self, tmp_path, capsys):
        from snodo.infrastructure.audit import AuditLog

        repo = _init_repo(tmp_path)
        audit = AuditLog(str(repo / ".snodo" / "audit.log"))
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged"), \
             patch("snodo.cli.commands.merge_cmd._audit_log", return_value=audit):
            args = SimpleNamespace(branches=["agent-a"], force=False, review="maybe", no_review=False)
            rc = merge_command(args)

        assert rc == 0
        reviews = audit.get_history("human_review_recorded")
        assert reviews[0].data["verdict"] == "unreviewed"
        assert "invalid --review" in capsys.readouterr().err

    def test_no_review_flag_skips_prompt_and_records_unreviewed(self, tmp_path, capsys, monkeypatch):
        from snodo.infrastructure.audit import AuditLog

        repo = _init_repo(tmp_path)
        audit = AuditLog(str(repo / ".snodo" / "audit.log"))
        # Even on a TTY, --no-review means unattended: never prompt.
        monkeypatch.setattr("snodo.cli.commands.merge_cmd.sys.stdin.isatty", lambda: True)
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged"), \
             patch("snodo.cli.commands.merge_cmd._audit_log", return_value=audit):
            args = SimpleNamespace(branches=["agent-a"], force=False, review=None, no_review=True)
            rc = merge_command(args)

        assert rc == 0
        reviews = audit.get_history("human_review_recorded")
        assert reviews[0].data["verdict"] == "unreviewed"

    def test_merge_from_repo_root_records_human_review_recorded(self, tmp_path, capsys):
        """A merge from a repo root (with unmocked _audit_log) creates/loads audit log and records review."""
        from snodo.infrastructure.audit import AuditLog

        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        # Do NOT patch _audit_log — let it resolve real AuditLog at repo root
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged"):
            args = SimpleNamespace(branches=["agent-a"], force=False, review="accepted", no_review=False)
            rc = merge_command(args)

        assert rc == 0
        audit_file = repo / ".snodo" / "audit.log"
        assert audit_file.exists()
        audit = AuditLog(str(audit_file))
        reviews = audit.get_history("human_review_recorded")
        assert len(reviews) == 1
        assert reviews[0].data["verdict"] == "accepted"
        assert "recorded review verdict 'accepted'" in capsys.readouterr().out

    def test_unresolvable_audit_log_warns_rather_than_throwing(self, tmp_path, capsys):
        """When _audit_log throws a resolution exception, merge completes with resolution warning rather than crashing."""
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged"), \
             patch("snodo.cli.commands.merge_cmd._audit_log", side_effect=RuntimeError("disk unwriteable")):
            args = SimpleNamespace(branches=["agent-a"], force=False, review="accepted", no_review=False)
            rc = merge_command(args)

        assert rc == 0
        err = capsys.readouterr().err
        assert "audit log resolution failed" in err
        assert "not recorded" in err

    def test_corrupted_audit_chain_reports_chain_error_distinctly(self, tmp_path, capsys):
        """When the audit log hash chain is corrupted, _record_merge_and_review reports AUDIT LOG CHAIN CORRUPTED distinctly from a resolution error."""
        from snodo.core.interfaces import AuditError

        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged"), \
             patch("snodo.cli.commands.merge_cmd._audit_log", side_effect=AuditError("hash chain broken on line 15")):
            args = SimpleNamespace(branches=["agent-a"], force=False, review="accepted", no_review=False)
            rc = merge_command(args)

        assert rc == 0
        err = capsys.readouterr().err
        assert "AUDIT LOG CHAIN CORRUPTED" in err
        assert "hash chain broken on line 15" in err
        assert "audit log resolution failed" not in err

    def test_real_corrupted_audit_file_reports_chain_error_on_merge(self, tmp_path, capsys):
        """A real corrupted audit log file on disk causes snodo merge to report AUDIT LOG CHAIN CORRUPTED."""
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        audit_file = repo / ".snodo" / "audit.log"
        audit_file.parent.mkdir(parents=True, exist_ok=True)
        audit_file.write_text("corrupted json payload\n")

        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches"), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value="merged"):
            args = SimpleNamespace(branches=["agent-a"], force=False, review="accepted", no_review=False)
            rc = merge_command(args)

        assert rc == 0
        err = capsys.readouterr().err
        assert "AUDIT LOG CHAIN CORRUPTED" in err
        assert "audit log resolution failed" not in err


class TestPushBranches:
    """snodo merge pushes all branches up front so CI runs concurrently, and
    force-pushes a diverged branch (recreated from main) instead of failing
    (Fixes #92)."""

    def test_pushes_all_branches_before_polling(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        _make_branch(repo, "agent-c")
        pushed = []

        def fake_push(repo, branches):
            pushed.extend(branches)

        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.resolve_base_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._current_branch", return_value="main"), \
             patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._count_new_commits", return_value=1), \
             patch("snodo.cli.commands.merge_cmd._push_branches", side_effect=fake_push) as push, \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")), \
             patch("snodo.cli.commands.merge_cmd.merge_task_branch", return_value=("merged", [])):
            rc = merge_command(SimpleNamespace(branches=["agent-a", "agent-c"], force=False))

        assert rc == 0
        # Both branches pushed up front, before any polling/merging.
        assert pushed == ["agent-a", "agent-c"]
        push.assert_called_once()

    def test_force_push_when_diverged(self, tmp_path, capsys):
        """A branch recreated from main (diverged from origin) is force-pushed
        with --force-with-lease, not treated as an error (Fixes #92)."""
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._branch_head_sha", return_value="local123"), \
             patch("snodo.cli.commands.merge_cmd._remote_tip", return_value="remote456"), \
             patch("snodo.cli.commands.merge_cmd._git") as git:
            git.return_value = SimpleNamespace(returncode=0, stderr="", stdout="")
            _push_branches(repo, ["agent-a"])

        args = git.call_args[0]
        assert args[1] == "push"
        assert "--force-with-lease" in args
        assert "agent-a" in args

    def test_plain_push_when_no_remote(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._branch_head_sha", return_value="local123"), \
             patch("snodo.cli.commands.merge_cmd._remote_tip", return_value=None), \
             patch("snodo.cli.commands.merge_cmd._git") as git:
            git.return_value = SimpleNamespace(returncode=0, stderr="", stdout="")
            _push_branches(repo, ["agent-a"])

        args = git.call_args[0]
        assert args[1] == "push"
        assert "-u" in args
        assert "--force-with-lease" not in args

    def test_skips_branch_already_on_origin(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        _make_branch(repo, "agent-a")
        with patch("snodo.cli.commands.merge_cmd._branch_exists", return_value=True), \
             patch("snodo.cli.commands.merge_cmd._branch_head_sha", return_value="same"), \
             patch("snodo.cli.commands.merge_cmd._remote_tip", return_value="same"), \
             patch("snodo.cli.commands.merge_cmd._git") as git:
            _push_branches(repo, ["agent-a"])

        git.assert_not_called()


class TestCiWait:
    """snodo ci-wait gates the MERGED result on CI (Fixes #92)."""

    def test_ci_wait_green_returns_zero(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("pass", "CI run 42 passed")):
            rc = ci_wait_command(SimpleNamespace(branch="main", timeout=900.0))

        assert rc == 0
        assert "CI green on merged main" in capsys.readouterr().out

    def test_ci_wait_red_returns_one(self, tmp_path, capsys):
        repo = _init_repo(tmp_path)
        with patch("snodo.cli.commands.merge_cmd._resolve_repo_root", return_value=str(repo)), \
             patch("snodo.cli.commands.merge_cmd.wait_for_ci_conclusion",
                   return_value=_conclusion("fail", "CI run 7 concluded 'failure'")):
            rc = ci_wait_command(SimpleNamespace(branch="main", timeout=900.0))

        assert rc == 1
        err = capsys.readouterr().err
        assert "not green" in err
        assert "break together" in err
