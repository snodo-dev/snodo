"""Commit provenance for the executor's empty and bounded-run recovery probe."""

import logging
import os
from typing import Any, Optional

from snodo.infrastructure.worktree import _name_component
from snodo.tools.git import resolve_base_branch

_logger = logging.getLogger(__name__)


def existing_task_branch_work(
    executor: Any,
    git_mcp: Any,
    base_ref: Optional[str] = None,
    *,
    refs_before: Optional[dict[str, str]] = None,
    task: Any = None,
) -> Optional[tuple[str, list[str]]]:
    """Recover run commits or older commits owned by this task branch.

    The dispatch snapshot is authoritative for new work. An unchanged retry
    can also recover older task-owned work, but only relative to the branch it
    actually forks from, never relative to a plan's historical base. Without
    that provenance a nonempty diff is ambiguous, not evidence of recovery.
    """
    if git_mcp is None:
        return None
    try:
        repo = git_mcp.repo
        branch = repo.active_branch.name
        if not executor._active_branch_is_task_branch(branch):
            return None
        head = repo.head.commit

        # Compare the exact snapshot, not its merge-base: inherited waves are
        # already in this commit. A retry may still have older task-owned work.
        start = (refs_before or {}).get(branch)
        if start:
            try:
                anchor = repo.commit(start)
                paths = _paths(anchor, head)
                if paths:
                    return anchor.hexsha, paths
            except Exception as exc:
                _logger.warning("Work-recovery probe: run-start ref unreadable: %s", exc)

        plan = getattr(task, "plan_name", None)
        target = os.environ.get("SNODO_PLAN_INTEGRATION_BRANCH") if plan else None
        if plan and not target:
            target = f"plan/{_name_component(plan)}/integration"
        if not plan:
            target = resolve_base_branch(str(git_mcp.project_root))
        try:
            bases = repo.merge_base(repo.commit(target), head)
            if not bases:
                raise ValueError("No merge-base with task merge target")
            anchor = bases[0]
        except Exception as exc:
            _logger.warning("Work-recovery probe: task merge target unresolved: %s", exc)
            fallback = base_ref or resolve_base_branch(str(git_mcp.project_root))
            bases = repo.merge_base(repo.commit(fallback), head)
            if bases and _paths(bases[0], head):
                # The probe failed to establish ownership. This is an
                # inspection failure, not a work_already_present assertion.
                executor._audit("execution_failed", {
                    "op": "work_recovery_probe",
                    "task_ref": getattr(task, "id", None),
                    "base_ref": bases[0].hexsha,
                    "verdict": "ambiguous",
                    "error": "Work recovery ambiguous: task merge target unresolved",
                })
                executor._progress(
                    "  Work-recovery probe is ambiguous: only the plan base "
                    "could be resolved; branch history is not evidence of "
                    "this run's output."
                )
            return None
        paths = _paths(anchor, head)
        return (anchor.hexsha, paths) if paths else None
    except Exception as exc:
        _logger.warning(
            "Work-recovery probe: branch/base inspection failed, "
            "skipping recovery: %s: %s", type(exc).__name__, exc,
        )
        return None


def _paths(anchor: Any, head: Any) -> list[str]:
    return sorted({
        change.b_path or change.a_path
        for change in anchor.diff(head)
        if change.b_path or change.a_path
    })
