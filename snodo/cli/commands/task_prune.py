"""Task prune command implementation.

FILE: snodo/cli/commands/task_prune.py
"""

import logging
import sys
from datetime import datetime, timezone, timedelta
from typing import Any

_logger = logging.getLogger(__name__)


def task_prune_command(args: Any) -> int:
    """List and delete stale task branches."""
    from snodo.cli.commands.task_cmd import _get_all_task_branches, resolve_project_root

    stale_days = getattr(args, "stale_days", 7)
    project_root = resolve_project_root()
    if project_root is None:
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    tasks = _get_all_task_branches(project_root)

    if not tasks:
        print("No task branches to prune.")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    stale = []
    skipped_untimestamped = 0
    for tid, info in sorted(tasks.items()):
        if not info["has_git_branch"] and not info.get("has_failure_context"):
            continue
        ts = info["timestamp"]
        if ts is None:
            skipped_untimestamped += 1
            continue
        if ts < cutoff:
            stale.append((tid, info["branch"], ts, info["has_git_branch"], info.get("has_failure_context", False)))

    if skipped_untimestamped > 0:
        print(f"Skipped {skipped_untimestamped} task(s) with unknown timestamp.")

    if not stale:
        print(f"No task branches older than {stale_days} days.")
        return 0

    print(f"Found {len(stale)} stale task branch(es) (> {stale_days} days):")
    print()
    for tid, branch, ts, _, _ in stale:
        print(f"  {tid}  {branch}  ({ts.strftime('%Y-%m-%d')})")
    print()

    try:
        answer = input(f"Delete these {len(stale)} branches? [y/N] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nAborted.")
        return 1
    if answer != "y":
        print("Aborted.")
        return 0

    try:
        from snodo.tools.git import GitMCP
        from snodo.infrastructure.worktree import remove_worktree
        from snodo.infrastructure.state import read_state
        from snodo.infrastructure.session import SessionManager

        git = GitMCP(project_root)

        state = read_state(project_root)
        mode = state.current_mode
        session = None
        mgr = None
        if mode:
            mgr = SessionManager()
            session = mgr.get_active_session(mode, project_root)

        deleted = 0
        for tid, branch, _, has_git, has_failure in stale:
            if git:
                branch_prefix = f"task/{tid}"
                for head in git.repo.heads:
                    if head.name == branch or head.name.startswith(branch_prefix):
                        git.repo.git.branch("-D", head.name)
                        deleted += 1
                        break
            remove_worktree(project_root, tid)

            if session and mgr and has_failure:
                task_failures = session.checkpoint.decisions.get("task_failure", {})
                if isinstance(task_failures, dict) and tid in task_failures:
                    del task_failures[tid]
                    try:
                        mgr.update_decision(session.session_id, "task_failure", task_failures)
                    except Exception as e:
                        _logger.warning("Could not clear failure context for task %s during prune: %s", tid, e)

        print(f"Deleted {deleted} stale branch(es).")
    except Exception as e:
        print(f"Error pruning branches: {e}", file=sys.stderr)
        return 1

    return 0
