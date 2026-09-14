"""A completed job tears down its worktree and branch; a failed job keeps both.

FILE: tests/jobs/test_job_worktree_teardown.py

Fixes #276.

#275 unified how a job's worktree is named: under the task identity derived
from the description, not the job id. The teardown was not moved with it — the
wrapper's success path still asked git to remove the *job id*, found nothing,
and left the real worktree (and the branch it held) behind a clean merge. Over
a real project that accumulated ~100 worktrees and ~100 merged task branches.

These tests drive the real wrapper ``main()`` against a real git repository and
assert the outcome the operator sees: nothing left behind on success, both
retained on failure.
"""

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest
from snodo.jobs import JobManager
from snodo.jobs.wrapper import main as wrapper_main
from snodo.paths import derive_task_id

DESCRIPTION = "teardown the task worktree"
TASK_ID = derive_task_id(DESCRIPTION)


@pytest.fixture
def git_project(tmp_path):
    """A real git repo with .snodo/ in a sibling-safe location."""
    root = tmp_path / "proj"
    root.mkdir()
    (root / ".snodo").mkdir()
    (root / ".gitignore").write_text(".snodo/\n.snodo-worktrees/\n")
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=root, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=root, check=True)
    (root / "README.md").write_text("init\n")
    subprocess.run(["git", "add", ".gitignore", "README.md"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=root, check=True)
    return root


def _worktree_path(project_root: Path) -> Path:
    return Path(project_root).parent / ".snodo-worktrees" / TASK_ID


def _task_branch(project_root: Path) -> str:
    out = subprocess.run(
        ["git", "branch", "--format=%(refname:short)"],
        cwd=project_root, capture_output=True, text=True, check=True,
    ).stdout
    return next((b for b in out.splitlines() if b.startswith(f"task/{TASK_ID}/")), "")


def _submit(project_root: Path) -> tuple:
    """Submit a real job (real worktree) without spawning the CLI."""
    manager = JobManager(str(project_root))
    with patch("snodo.jobs.runner.spawn_background", return_value=99999):
        job_id = manager.submit({
            "description": DESCRIPTION,
            "protocol": ".snodo/protocol.yml",
            "model": None,
            "mock": True,
            "verbose": False,
            "from_pr": None,
            "cwd": str(project_root),
        })
    return manager, job_id, manager.jobs_dir / job_id


def _run_wrapper(job_dir: Path, exit_code: int) -> None:
    """Run the wrapper's main() for an already-submitted job, faking the CLI."""
    with patch.object(sys, "argv", ["wrapper", str(job_dir), "run", DESCRIPTION]):
        with patch("snodo.jobs.wrapper.subprocess.run") as mock_run:
            mock_run.return_value.returncode = exit_code
            with pytest.raises(SystemExit):
                wrapper_main()


def _commit_on_branch(project_root: Path, branch: str) -> None:
    """Commit a change on *branch* so it has work worth merging."""
    from snodo.infrastructure.worktree import worktree_path
    wt = worktree_path(str(project_root), TASK_ID)
    (wt / "feature.txt").write_text("feature\n")
    subprocess.run(["git", "add", "feature.txt"], cwd=wt, check=True)
    subprocess.run(["git", "commit", "-qm", "feature"], cwd=wt, check=True)


class TestCompletedJobTearsDown:
    def test_completed_job_leaves_no_worktree_and_no_branch(self, git_project):
        """A job that completes cleanly leaves neither its worktree nor branch."""
        from snodo.infrastructure.worktree import merge_task_branch

        manager, job_id, job_dir = _submit(git_project)
        branch = _task_branch(git_project)
        _commit_on_branch(git_project, branch)
        merge_task_branch(str(git_project), branch)

        assert _worktree_path(git_project).exists()
        assert branch

        _run_wrapper(job_dir, exit_code=0)

        assert not _worktree_path(git_project).exists()
        assert _task_branch(git_project) == ""


class TestFailedJobPreserves:
    def test_failed_job_keeps_worktree_and_branch(self, git_project):
        """A failed job keeps both for inspection — its work may be unmerged."""
        _manager, _job_id, job_dir = _submit(git_project)

        _run_wrapper(job_dir, exit_code=1)

        assert _worktree_path(git_project).exists()
        assert _task_branch(git_project) != ""


class TestUnmergedJobPreservesBranch:
    def test_completed_but_unmerged_branch_survives(self, git_project):
        """Success does not discard a branch whose work is not in the base.

        A completed job whose branch was never merged (auto-merge off, or a
        refused merge) must keep the only copy of that work.
        """
        _manager, _job_id, job_dir = _submit(git_project)
        branch = _task_branch(git_project)
        _commit_on_branch(git_project, branch)

        _run_wrapper(job_dir, exit_code=0)

        assert not _worktree_path(git_project).exists()
        assert _task_branch(git_project) == branch


class TestOnRepositoryFullOfDebris:
    def test_completed_job_cleans_its_own_amid_existing_debris(self, git_project):
        """The fix works on a repo already leaking worktrees from older jobs.

        Existing debris is the operator's to clear, not this change's — but a
        fix that cannot be shown to work beside it has not been shown to work.
        A completed job must leave nothing new while the pre-existing leak is
        left for the operator to remove.
        """
        from snodo.infrastructure.worktree import merge_task_branch

        pre_existing = []
        for i in range(5):
            tid = f"task_debris{i:04d}"
            wt = Path(git_project).parent / ".snodo-worktrees" / tid
            subprocess.run(
                ["git", "worktree", "add", "-q", str(wt), "-b", f"task/{tid}/old", "main"],
                cwd=git_project, check=True,
            )
            pre_existing.append(wt)

        _manager, _job_id, job_dir = _submit(git_project)
        branch = _task_branch(git_project)
        _commit_on_branch(git_project, branch)
        merge_task_branch(str(git_project), branch)

        _run_wrapper(job_dir, exit_code=0)

        assert not _worktree_path(git_project).exists()
        assert _task_branch(git_project) == ""
        assert all(wt.exists() for wt in pre_existing)


class TestWorktreeNameHasOneHome:
    def test_creation_and_teardown_compute_the_same_identity(self, git_project):
        """The name a worktree is created under is the name teardown uses.

        Both the submit path and the wrapper resolve the identity through
        ``resolve_task_identity``. This asserts they yield the same value for
        the same task.json, which is what a second, independent computation
        would break.
        """
        from snodo.jobs import resolve_task_identity

        _manager, job_id, job_dir = _submit(git_project)
        task_data = json.loads((job_dir / "task.json").read_text())

        stored = task_data["task_id"]
        wrapper_identity = resolve_task_identity(task_data, job_id)

        assert stored == wrapper_identity == TASK_ID

    def test_resolver_ignores_the_job_id_when_a_description_exists(self):
        """The identity is the description's, not the job id — the drift that
        left the real worktree registered was teardown using the job id."""
        from snodo.jobs import resolve_task_identity

        assert resolve_task_identity({"description": DESCRIPTION}, "j_deadbeef") == TASK_ID

    def test_plan_run_has_no_task_identity(self):
        from snodo.jobs import resolve_task_identity

        assert resolve_task_identity({"plan_name": "ship"}, "j_plan01") is None
