"""Task branch merge operations on successful task completion.

FILE: snodo/cli/commands/run_merge.py
"""

import logging
import sys
from pathlib import Path
from typing import Any, Optional

from snodo.compiler.models import Protocol
from snodo.core.interfaces import Task
from snodo.tools.git import open_repo

_logger = logging.getLogger(__name__)


def _verified_commit_matches_merge_target(stored_commit: str, target_commit: str) -> bool:
    """Whether a verification event's stored commit evidences the merge target.

    Real payloads carry a full SHA, so exact equality is the intended match. A
    stored value that merely abbreviates the target (a prefix of it) is also
    accepted. The reverse direction is deliberately NOT accepted: a stored
    value that has the target as its own prefix could denote a different,
    longer commit, so it must not satisfy the gate (Refs #206).
    """
    if not stored_commit or not target_commit:
        return False
    return stored_commit == target_commit or target_commit.startswith(stored_commit)


def _merge_on_success(project_root: str, task: Any, result: int, session_id: Optional[str], audit_log: Any) -> tuple:
    """Merge the completed task's branch into the base branch.

    Returns (result, preserve_worktree, merged_branch). On a clean merge the
    branch is queued for deletion (after the worktree is removed) and the
    worktree is left for the caller's normal teardown. On a conflict the task
    is escalated: the branch and worktree survive for a human to resolve.
    """
    from snodo.infrastructure.worktree import (
        task_branch_name,
        merge_task_branch,
        merge_head_sha,
        merge_lock,
        stale_index_lock,
    )
    from snodo.tools.git import GitError

    spec_for_branch = getattr(task, "root_spec", None) or task.spec
    branch = task_branch_name(task.id, spec_for_branch)

    with merge_lock(project_root):
        # Resolve target commit on the branch to be merged
        target_commit = ""
        try:
            with open_repo(str(Path(project_root))) as repo:
                target_commit = repo.commit(branch).hexsha
        except Exception as e:
            _logger.debug("Could not resolve commit for branch %s: %s", branch, e)

        if audit_log:
            history = audit_log.get_history("verification_executed")
            matching = [
                e for e in history
                if target_commit
                and _verified_commit_matches_merge_target(e.data.get("commit"), target_commit)
            ]
            matching_passes = [e for e in matching if e.data.get("outcome") == "pass"]
            matching_ungated = [e for e in matching if e.data.get("outcome") == "no_tests"]
            if not matching_passes and not matching_ungated:
                commit_display = target_commit[:7] if target_commit else "unknown"
                print(f"✗ Refused merge for {branch}: no passing verification_executed event for task {task.id} at commit {commit_display}.", file=sys.stderr)
                print("  An unverified merge is forbidden. Worktree and branch left intact.", file=sys.stderr)
                audit_log.append_event("unverified_merge_blocked", {
                    "op": "unverified_merge_blocked",
                    "task_ref": task.id,
                    "branch": branch,
                    "target_commit": target_commit,
                    "reason": f"No passing verification_executed event recorded for task {task.id} at commit {commit_display}.",
                    "session_id": session_id,
                })
                return 1, True, None

            if matching_passes:
                accepted_event = matching_passes[-1]
                commit_display = target_commit[:7] if target_commit else "unknown"
                cmd = accepted_event.data.get("command", "")
                print(f"✓ Verified merge for {branch}: task {task.id} verified at commit {commit_display} ({cmd}).", file=sys.stderr)
            else:
                commit_display = target_commit[:7] if target_commit else "unknown"
                print(f"✓ Merged {branch} ungated: task {task.id} at commit {commit_display} ran no tests (no test_command configured).", file=sys.stderr)

        try:
            res = merge_task_branch(project_root, branch)
            if isinstance(res, tuple):
                outcome, conflicting_paths = res
            else:
                outcome, conflicting_paths = res, []
        except GitError as e:
            print(f"✗ Merge failed for {branch}: {e}", file=sys.stderr)
            if stale_index_lock(project_root, e):
                print("  Stale Git index lock: no process is holding .git/index.lock. Clear it with: rm .git/index.lock", file=sys.stderr)
            print("  The branch and worktree were left intact for manual resolution.", file=sys.stderr)
            if audit_log:
                audit_log.append_event("merge_failed_escalated", {
                    "op": "merge_failed_escalated",
                    "task_ref": task.id,
                    "branch": branch,
                    "error": str(e),
                    "session_id": session_id,
                })
            return 1, True, None

        if outcome == "merged":
            if audit_log:
                authoritative_spec = getattr(task, "root_spec", None) or getattr(task, "spec", "")
                audit_log.append_event("task_merged", {
                    "op": "task_merged",
                    "task_ref": task.id,
                    "branch": branch,
                    "merge_sha": merge_head_sha(project_root),
                    "session_id": session_id,
                    "spec": authoritative_spec,
                })
            print(f"✓ Merged {branch} into the base branch")
            return result, False, branch

        paths_str = ", ".join(conflicting_paths) if conflicting_paths else "unknown path(s)"
        print(f"✗ Merge conflict merging {branch} into the base branch.", file=sys.stderr)
        print(f"  Conflicting path(s): {paths_str}", file=sys.stderr)
        print("  The merge was rolled back (base branch left clean; source branch intact).", file=sys.stderr)
        print(f"  To perform the merge manually and resolve conflicts, run:\n    git merge {branch}", file=sys.stderr)
        if audit_log:
            audit_log.append_event("merge_conflict_escalated", {
                "op": "merge_conflict_escalated",
                "task_ref": task.id,
                "branch": branch,
                "conflicting_paths": conflicting_paths,
                "session_id": session_id,
            })
        return 1, True, None


def _try_merge_unmerged_task(
    project_root: str,
    task_id: str,
    spec: str,
    protocol: Optional[Protocol] = None,
    session_id: Optional[str] = None,
    audit_log: Optional[Any] = None,
) -> Optional[bool]:
    """Attempt fast-path merge of an unmerged task branch.

    Returns:
        True: Branch existed, passed merge gate, and was merged successfully.
        False: Branch existed and passed gate, but merge failed (e.g. lock or conflict).
        None: Branch does not exist or does not pass merge gate (cannot fast-path merge).
    """
    from snodo.infrastructure.worktree import (
        task_branch_name,
        teardown_task_worktree,
    )
    branch = task_branch_name(task_id, spec)
    try:
        with open_repo(str(Path(project_root))) as repo:
            if branch not in repo.heads:
                return None
            target_commit = repo.commit(branch).hexsha
    except Exception as e:
        _logger.debug("Could not resolve branch %s for fast-path merge: %s", branch, e)
        return None

    if audit_log is None:
        from snodo.infrastructure.audit import AuditLog
        audit_log_path = Path(project_root) / ".snodo" / "audit.log"
        if audit_log_path.exists():
            audit_log = AuditLog(str(audit_log_path))

    if not audit_log:
        return None

    history = audit_log.get_history("verification_executed")
    matching = [
        e for e in history
        if target_commit
        and _verified_commit_matches_merge_target(e.data.get("commit"), target_commit)
    ]
    matching_passes = [e for e in matching if e.data.get("outcome") == "pass"]
    matching_ungated = [e for e in matching if e.data.get("outcome") == "no_tests"]
    if not matching_passes and not matching_ungated:
        return None

    from snodo.cli.commands.task_record import _record_task_completion

    task = Task(id=task_id, spec=spec)
    merge_result, preserve_worktree, merged_branch = _merge_on_success(
        project_root, task, 0, session_id, audit_log
    )
    if merge_result == 0 and merged_branch:
        try:
            teardown_task_worktree(project_root, task_id)
        except Exception as e:
            _logger.debug("Could not tear down worktree after merge for %s: %s", task_id, e)
        _record_task_completion(project_root, task_id, "completed")
        return True
    _record_task_completion(project_root, task_id, "unmerged")
    return False
