"""Reporting for resolved tasks whose changes remain unmerged."""

import sys
from typing import Optional


def _report_unmerged_branch(project_root, task, protocol, mode, closure_tree, worktree_path_val, worktree_degraded, session_id, audit_log, plan_name: Optional[str] = None) -> None:
    """Report a resolved task whose branch was NOT merged, and audit it."""
    from snodo.cli.commands.run_cmd import _auto_merge_block_reason

    reason = _auto_merge_block_reason(
        protocol, mode, closure_tree, worktree_path_val, worktree_degraded,
        project_root=project_root, task=task, plan_name=plan_name,
    )
    if reason is None or reason == "no changes on task branch":
        return
    from snodo.infrastructure.worktree import _task_identity
    spec = getattr(task, "root_spec", None) or task.spec
    _, branch = _task_identity(project_root, task.id, spec, plan_name)
    print("⚠ Task resolved but its work was NOT merged to the base branch.", file=sys.stderr)
    print(f"  Reason: {reason}", file=sys.stderr)
    if worktree_degraded or not worktree_path_val:
        print("  Isolation was degraded, so the changes are in your working tree; commit them there.", file=sys.stderr)
    else:
        print(f"  Branch holding the work: {branch}", file=sys.stderr)
        print(f"  This is the configured behavior. Merge it now with: git merge {branch}", file=sys.stderr)
        print("  Change delivery in .snodo/protocol.yml under execution.delivery or the active mode's delivery setting.", file=sys.stderr)
    if audit_log:
        from snodo.cli.commands.run_merge import _plan_task_fields
        audit_log.append_event("task_unmerged", {
            "op": "task_unmerged", "task_ref": task.id, "branch": branch,
            "reason": reason, "session_id": session_id,
            **_plan_task_fields(project_root, plan_name, task.id, task),
        })
