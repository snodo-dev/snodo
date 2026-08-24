"""Task command — snodo task list / abandon / prune.

FILE: snodo/cli/commands/task_cmd.py
"""

import sys
from types import SimpleNamespace

import typer

from snodo.infrastructure.paths import resolve_project_root

# ---------------------------------------------------------------------------
# Self-registering Typer app (discovered by snodo/cli/main.py discovery loop)
# ---------------------------------------------------------------------------

COMMAND_NAME = "task"

app = typer.Typer(invoke_without_command=True, help="Manage task branches")


@app.callback()
def _task_callback(ctx: typer.Context):
    """Manage task branches."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@app.command(name="list")
def task_list():
    """List all task branches in the current project."""
    return task_list_command(SimpleNamespace())


@app.command(name="show")
def task_show(
    task_id: str = typer.Argument(..., help="Task ID to inspect (e.g. task_a1b2c3)"),
):
    """Inspect a task's halt and failure record from the active session."""
    return task_show_command(SimpleNamespace(task_id=task_id))


@app.command(name="abandon")
def task_abandon(
    task_id: str = typer.Argument(..., help="Task ID to abandon (e.g. task_a1b2c3)"),
):
    """Delete a task branch and clear its failure context."""
    return task_abandon_command(SimpleNamespace(task_id=task_id))


@app.command(name="prune")
def task_prune(
    stale_days: int = typer.Option(7, "--stale-days", help="Days without activity before pruning"),
):
    """List and delete stale task branches."""
    return task_prune_command(SimpleNamespace(stale_days=stale_days))



def task_list_command(args) -> int:
    """List all task branches in the current project with status."""
    project_root = resolve_project_root()
    if project_root is None:
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    from snodo.infrastructure.state import read_state
    from snodo.infrastructure.session import SessionManager

    state = read_state(project_root)
    mode = state.current_mode

    task_failures: dict = {}

    if mode:
        mgr = SessionManager()
        session = mgr.get_active_session(mode, project_root)
        if session:
            task_failures = session.checkpoint.decisions.get("task_failure", {})
            if not isinstance(task_failures, dict):
                task_failures = {}

    if not task_failures:
        print("No task branches in current session.")
        return 0

    print(f"{'TASK ID':<14} {'BRANCH':<50} {'ATTEMPT':<8} {'STATUS'}")
    print("-" * 86)

    for tid, ctx in sorted(task_failures.items()):
        branch = ctx.get("branch", "—")
        attempt = ctx.get("attempt", 0)
        status = "failed"
        print(f" {tid:<14} {branch:<50} {attempt:<8} {status}")
        print(f"   inspect: snodo task show {tid}")

    print()
    print("Use snodo task abandon <task_id> to delete a task branch.")
    return 0


def task_show_command(args) -> int:
    """Inspect a task's halt and failure record from the active session."""
    task_id = getattr(args, "task_id", "")
    if not task_id:
        print("Usage: snodo task show <task_id>", file=sys.stderr)
        return 1

    project_root = resolve_project_root()
    if project_root is None:
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    from snodo.infrastructure.state import read_state
    from snodo.infrastructure.session import SessionManager

    state = read_state(project_root)
    mode = state.current_mode
    if not mode:
        print("No active mode. Run 'snodo mode change <m>' first.", file=sys.stderr)
        return 1

    mgr = SessionManager()
    session = mgr.get_active_session(mode, project_root)
    if session is None:
        print(f"No active session for mode={mode}.", file=sys.stderr)
        return 1

    decisions = session.checkpoint.decisions or {}
    halt = decisions.get("halt", {})
    failure = decisions.get("task_failure", {})

    halt_entry = halt.get(task_id) if isinstance(halt, dict) else None
    failure_entry = failure.get(task_id) if isinstance(failure, dict) else None

    if not halt_entry and not failure_entry:
        print(f"No record for task {task_id} in session {session.session_id}.")
        return 1

    print(f"Task:    {task_id}")
    print(f"Session: {session.session_id}  mode={session.mode}")

    if isinstance(halt_entry, dict):
        print()
        print("Halt:")
        print(f"  final_decision: {halt_entry.get('final_decision', 'unknown')}")
        print(f"  halt_type:      {halt_entry.get('halt_type', 'unknown')}")
        print(f"  phase:          {halt_entry.get('phase', 'unknown')}")
        reason = halt_entry.get("reason") or halt_entry.get("blocker_reason")
        if reason:
            print(f"  reason:         {reason}")
        hint = halt_entry.get("hint")
        if hint:
            print(f"  hint:           {hint}")
        validator_results = halt_entry.get("validator_results", [])
        if validator_results:
            print("  validators:")
            for r in validator_results:
                print(f"    {r.get('validator_id', '?')} [{r.get('severity', '?')}]: {r.get('justification', '')}")

    if isinstance(failure_entry, dict):
        print()
        print("Failure context:")
        print(f"  attempt: {failure_entry.get('attempt', 0)}")
        branch = failure_entry.get("branch")
        if branch:
            print(f"  branch:  {branch}")
        files = failure_entry.get("files_changed", [])
        if files:
            print(f"  files:   {', '.join(files)}")

    print()
    print("Inspect:")
    print(f"  snodo session show {session.session_id}")
    if isinstance(failure_entry, dict):
        print(f'  snodo run --retry {task_id} "revised spec"')
    return 0


def task_abandon_command(args) -> int:
    """Delete a task branch and clear its failure context."""
    task_id = getattr(args, "task_id", "")
    if not task_id:
        print("Usage: snodo task abandon <task_id>", file=sys.stderr)
        return 1

    project_root = resolve_project_root()
    if project_root is None:
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    # Clear failure context from session
    from snodo.infrastructure.state import read_state
    from snodo.infrastructure.session import SessionManager

    state = read_state(project_root)
    mode = state.current_mode
    if mode:
        mgr = SessionManager()
        session = mgr.get_active_session(mode, project_root)
        if session:
            task_failures = session.checkpoint.decisions.get("task_failure", {})
            if isinstance(task_failures, dict) and task_id in task_failures:
                del task_failures[task_id]
                try:
                    mgr.update_decision(
                        session.session_id, "task_failure", task_failures,
                    )
                except Exception:
                    pass

    # Delete the branch
    try:
        from snodo.tools.git import GitMCP
        git = GitMCP(project_root)
        branch_name = f"task/{task_id}"
        for head in git.repo.heads:
            if head.name.startswith(branch_name):
                git.repo.git.branch("-D", head.name)
    except Exception as e:
        print(f"Error deleting branch: {e}", file=sys.stderr)
        return 1

    # Remove worktree
    try:
        from snodo.infrastructure.worktree import remove_worktree
        remove_worktree(project_root, task_id)
    except Exception:
        pass

    print("Task abandoned.")
    return 0


def task_prune_command(args) -> int:
    """List and delete stale task branches."""
    from datetime import datetime, timezone, timedelta

    stale_days = getattr(args, "stale_days", 7)
    project_root = resolve_project_root()
    if project_root is None:
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    from snodo.infrastructure.state import read_state
    from snodo.infrastructure.session import SessionManager

    state = read_state(project_root)
    mode = state.current_mode
    task_failures: dict = {}

    if mode:
        mgr = SessionManager()
        session = mgr.get_active_session(mode, project_root)
        if session:
            task_failures = session.checkpoint.decisions.get("task_failure", {})
            if not isinstance(task_failures, dict):
                task_failures = {}

    if not task_failures:
        print("No task branches to prune.")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=stale_days)
    stale = []
    for tid, ctx in sorted(task_failures.items()):
        ts_str = ctx.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            ts = datetime.now(timezone.utc)
        if ts < cutoff:
            stale.append((tid, ctx.get("branch", ""), ts))

    if not stale:
        print(f"No task branches older than {stale_days} days.")
        return 0

    print(f"Found {len(stale)} stale task branch(es) (> {stale_days} days):")
    print()
    for tid, branch, ts in stale:
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
        git = GitMCP(project_root)
        deleted = 0
        for tid, branch, _ in stale:
            branch_prefix = f"task/{tid}"
            for head in git.repo.heads:
                if head.name.startswith(branch_prefix):
                    git.repo.git.branch("-D", head.name)
                    deleted += 1
                    break
            remove_worktree(project_root, tid)
        print(f"Deleted {deleted} stale branch(es).")
    except Exception as e:
        print(f"Error pruning branches: {e}", file=sys.stderr)
        return 1

    return 0
