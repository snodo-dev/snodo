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

_MERGE_COMMIT_LIMIT = 50


def _merge_base_sha(project_root: str) -> str:
    """Return the current base HEAD before a task branch is merged."""
    try:
        with open_repo(str(Path(project_root))) as repo:
            return repo.head.commit.hexsha
    except Exception as e:
        _logger.debug("Could not resolve base HEAD before merge: %s", e)
        return ""


def _merge_delivery(project_root: str, base_sha: str, merge_sha: str) -> dict:
    """Measure the work Snodo merged, or return no metrics on any read failure."""
    if not base_sha or not merge_sha:
        return {}
    try:
        with open_repo(str(Path(project_root))) as repo:
            commits = list(repo.iter_commits(f"{base_sha}..{merge_sha}"))
            numstat = repo.git.diff("--numstat", base_sha, merge_sha)
            files_changed = insertions = deletions = 0
            for line in numstat.splitlines():
                fields = line.split("\t", 2)
                if len(fields) != 3:
                    raise ValueError(f"Unexpected git numstat line: {line!r}")
                files_changed += 1
                if fields[0] != "-":
                    insertions += int(fields[0])
                if fields[1] != "-":
                    deletions += int(fields[1])

            result = {
                "base_sha": base_sha,
                "commit_count": len(commits),
                "commits": [
                    {"sha": commit.hexsha, "subject": commit.message.splitlines()[0] if commit.message else ""}
                    for commit in commits[:_MERGE_COMMIT_LIMIT]
                ],
                "files_changed": files_changed,
                "insertions": insertions,
                "deletions": deletions,
            }
            if len(commits) > _MERGE_COMMIT_LIMIT:
                result["commits_truncated"] = True
            return result
    except Exception as e:
        _logger.debug("Could not measure delivered merge %s..%s: %s", base_sha, merge_sha, e)
        return {}


def _plan_task_fields(project_root: str, plan_name: Optional[str], task_id: str, task: Any = None) -> dict:
    """Return local history ownership fields for a task in a plan."""
    if not plan_name:
        return {}
    wave_id = getattr(task, "plan_wave", None)
    if wave_id is None:
        try:
            import yaml
            plan_file = Path(project_root) / ".snodo" / "plans" / plan_name / "plan.yml"
            plan_data = yaml.safe_load(plan_file.read_text()) or {}
            wave_id = next(
                (wave.get("id") for wave in plan_data.get("waves", [])
                 if task_id in wave.get("tasks", [])),
                None,
            )
        except (OSError, ValueError, TypeError):
            wave_id = None
    if wave_id is None:
        return {}
    return {"plan_name": plan_name, "plan_wave": str(wave_id)}


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


def _merge_on_success(
    project_root: str,
    task: Any,
    result: int,
    session_id: Optional[str],
    audit_log: Any,
    plan_name: Optional[str] = None,
    delivery: str = "local_merge",
    remote: str = "origin",
    protocol_metadata: Optional[dict] = None,
) -> tuple:
    """Merge the completed task's branch into the base branch.

    Returns (result, preserve_worktree, merged_branch). On a clean merge the
    branch is queued for deletion (after the worktree is removed) and the
    worktree is left for the caller's normal teardown. On a conflict the task
    is escalated: the branch and worktree survive for a human to resolve.
    """
    from snodo.infrastructure.worktree import (
        merge_task_branch,
        merge_head_sha,
        merge_lock,
        stale_index_lock,
    )
    from snodo.tools.git import GitError

    spec_for_branch = getattr(task, "root_spec", None) or task.spec
    from snodo.infrastructure.worktree import _task_identity
    _, branch = _task_identity(project_root, task.id, spec_for_branch, plan_name)

    target_commit = ""
    try:
        with open_repo(str(Path(project_root))) as repo:
            target_commit = repo.commit(branch).hexsha
    except Exception as e:
        _logger.debug("Could not resolve commit for branch %s: %s", branch, e)

    if delivery in {"push_branch", "change_request"} or (delivery == "local_merge" and audit_log):
        history = audit_log.get_history("verification_executed") if audit_log else []
        matching = [
            e for e in history
            if target_commit and _verified_commit_matches_merge_target(
                e.data.get("commit"), target_commit
            )
        ]
        passing = [e for e in matching if e.data.get("outcome") in {"pass", "no_tests"}]
        if not passing:
            commit_display = target_commit[:7] if target_commit else "unknown"
            reason = f"No passing verification_executed event recorded for task {task.id} at commit {commit_display}."
            action = "merge" if delivery == "local_merge" else "delivery"
            print(f"✗ Refused {action} for {branch}: {reason}", file=sys.stderr)
            if audit_log:
                audit_log.append_event("unverified_merge_blocked", {
                    "op": "unverified_merge_blocked", "task_ref": task.id,
                    "branch": branch, "target_commit": target_commit,
                    "reason": reason, "session_id": session_id,
                })
            return 1, True, None
        accepted = passing[-1]
        commit_display = target_commit[:7] if target_commit else "unknown"
        verb = "merge" if delivery == "local_merge" else "delivery"
        if accepted.data.get("outcome") == "pass":
            print(
                f"✓ Verified {verb} for {branch}: task {task.id} verified at commit "
                f"{commit_display} ({accepted.data.get('command', '')}).",
                file=sys.stderr,
            )
        else:
            print(
                f"✓ Merged {branch} ungated: task {task.id} at commit {commit_display} "
                "ran no tests (no test_command configured).",
                file=sys.stderr,
            )

    if delivery in {"push_branch", "change_request"}:
        try:
            with open_repo(str(Path(project_root))) as repo:
                repo.git.push(remote, branch)
            print(f"✓ Pushed {branch} to {remote}")
            if delivery == "change_request":
                from snodo.providers.registry import detect_provider
                provider = detect_provider(project_root, protocol_metadata)
                from snodo.providers.local import LocalProvider
                if isinstance(provider, LocalProvider):
                    raise RuntimeError("no code-host provider is configured; install/configure a provider plugin and its credentials")
                with open_repo(str(Path(project_root))) as repo:
                    target_branch = repo.active_branch.name
                reference = provider.create_change_request(
                    branch, task.spec, f"Task {task.id}: {task.spec}\n\nTask id: {task.id}",
                    target_branch=target_branch,
                )
                return result, False, (branch, reference)
            return result, False, branch
        except Exception as e:
            print(f"✗ Delivery failed for {branch}: {e}", file=sys.stderr)
            print("  The branch and worktree were left intact for manual resolution.", file=sys.stderr)
            if audit_log:
                reason = (
                    f"Change-request delivery failed: {e}. Check the configured code-host "
                    "plugin and credentials."
                    if delivery == "change_request"
                    else f"Push to {remote} failed: {e}"
                )
                audit_log.append_event("task_unmerged", {
                    "op": "task_unmerged", "task_ref": task.id, "branch": branch,
                    "reason": reason, "session_id": session_id,
                    **_plan_task_fields(project_root, plan_name, task.id, task),
                })
            return 1, True, None

    with merge_lock(project_root):
        base_sha = _merge_base_sha(project_root)

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
                merge_sha = merge_head_sha(project_root)
                audit_log.append_event("task_merged", {
                    "op": "task_merged",
                    "task_ref": task.id,
                    "branch": branch,
                    "merge_sha": merge_sha,
                    "session_id": session_id,
                    "spec": authoritative_spec,
                    **_merge_delivery(project_root, base_sha, merge_sha),
                    **_plan_task_fields(project_root, plan_name, task.id, task),
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


def _deliver_on_success(
    project_root: str, task: Any, result: int, session_id: Optional[str],
    audit_log: Any, protocol: Protocol, mode: str, halt_payload: Any,
    model: str, plan_name: Optional[str] = None, merge_operation: Any = None,
) -> tuple:
    """Deliver resolved work and persist the resulting task outcome."""
    deliver = merge_operation or _merge_on_success
    merge_result, preserve, branch = deliver(
        project_root, task, result, session_id, audit_log,
        plan_name=plan_name, delivery=protocol.delivery_for(mode),
        remote=getattr(protocol.execution, "delivery_remote", "origin"),
        protocol_metadata=protocol.metadata,
    )
    from snodo.cli.commands.task_record import _record_task_completion

    if merge_result:
        _record_task_completion(
            project_root, task.id, "unmerged", halt_payload,
            protocol=protocol, model=model,
        )
        return 2, preserve, branch
    change_request_reference = None
    if isinstance(branch, tuple):
        branch, change_request_reference = branch
    if protocol.delivery_for(mode) in {"push_branch", "change_request"} and branch:
        _record_task_completion(
            project_root, task.id, "completed", halt_payload,
            protocol=protocol, model=model, delivered_branch=branch,
            change_request_reference=change_request_reference,
        )
    return merge_result, preserve, branch


def _try_merge_unmerged_task(
    project_root: str,
    task_id: str,
    spec: str,
    protocol: Optional[Protocol] = None,
    session_id: Optional[str] = None,
    audit_log: Optional[Any] = None,
    plan_name: Optional[str] = None,
) -> Optional[bool]:
    """Attempt fast-path merge of an unmerged task branch.

    Returns:
        True: Branch existed, passed merge gate, and was merged successfully.
        False: Branch existed and passed gate, but merge failed (e.g. lock or conflict).
        None: Branch does not exist or does not pass merge gate (cannot fast-path merge).
    """
    from snodo.infrastructure.worktree import teardown_task_worktree
    from snodo.infrastructure.worktree import _task_identity
    _, branch = _task_identity(project_root, task_id, spec, plan_name)
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
        project_root, task, 0, session_id, audit_log, plan_name=plan_name
    )
    if merge_result == 0 and merged_branch:
        try:
            teardown_task_worktree(project_root, task_id, plan_name)
        except Exception as e:
            _logger.debug("Could not tear down worktree after merge for %s: %s", task_id, e)
        _record_task_completion(project_root, task_id, "completed")
        return True
    _record_task_completion(project_root, task_id, "unmerged")
    return False
