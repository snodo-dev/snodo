"""Worktree command — list / remove / prune retained task worktrees.

FILE: snodo/cli/commands/worktree_cmd.py

A task that does not complete (or one run with ``--retain-worktree``) leaves its
worktree behind for inspection. These commands let the operator see what has
accumulated and clean it up, and ``prune`` uses the protocol's
``execution.branch_ttl_days`` so nothing accumulates silently.
"""

import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Optional

import typer

from snodo.infrastructure.paths import require_project_root
from snodo.infrastructure.worktree import list_worktrees, remove_worktree, worktree_path

_logger = logging.getLogger(__name__)

COMMAND_NAME = "worktree"

app = typer.Typer(invoke_without_command=True, help="Manage retained task worktrees")


@app.callback()
def _worktree_callback(ctx: typer.Context):
    """Manage retained task worktrees."""
    if ctx.invoked_subcommand is None:
        print(ctx.get_help())


@app.command("list")
def worktree_list(
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """List retained task worktrees."""
    return worktree_list_command(SimpleNamespace(json=json))


@app.command("remove")
def worktree_remove(
    task_id: str = typer.Argument(..., help="Task id of the retained worktree to remove"),
):
    """Remove a retained task worktree and its branch."""
    return worktree_remove_command(SimpleNamespace(task_id=task_id))


@app.command("prune")
def worktree_prune(
    days: Optional[int] = typer.Option(
        None, "--days", help="Remove worktrees older than this many days (default: protocol branch_ttl_days)",
    ),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force removal without confirmation or failure protection",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="List worktrees that would be removed without deleting them",
    ),
):
    """Remove retained worktrees older than the TTL (protocol branch_ttl_days)."""
    return worktree_prune_command(SimpleNamespace(days=days, force=force, dry_run=dry_run))


def _default_ttl_days(project_root: str) -> int:
    """Read execution.branch_ttl_days from .snodo/protocol.yml, else 7."""
    protocol_path = Path(project_root) / ".snodo" / "protocol.yml"
    if protocol_path.exists():
        try:
            from snodo.cli.commands import load_protocol
            protocol = load_protocol(protocol_path)
            if protocol is not None:
                return getattr(protocol.execution, "branch_ttl_days", 7)
        except Exception as e:
            _logger.debug("Could not read branch_ttl_days from protocol: %s", e)
    return 7


def _has_failure_context(project_root: str, task_id: str) -> bool:
    """Return True if task_id has a failure or halt record in active session."""
    try:
        from snodo.infrastructure.state import read_state
        from snodo.infrastructure.session import SessionManager

        state = read_state(project_root)
        mode = state.current_mode
        if not mode:
            return False
        mgr = SessionManager()
        session = mgr.get_active_session(mode, project_root)
        if not session:
            return False
        decisions = session.checkpoint.decisions or {}
        tf = decisions.get("task_failure", {})
        if isinstance(tf, dict) and task_id in tf:
            return True
        h = decisions.get("halt", {})
        if isinstance(h, dict) and task_id in h:
            entry = h[task_id]
            if isinstance(entry, dict) and entry.get("status") in ("blocked", "failed"):
                return True
    except Exception as e:
        _logger.debug("Could not inspect failure context for task %s: %s", task_id, e)
    return False


def worktree_list_command(args) -> int:
    project_root = require_project_root()
    names = list_worktrees(project_root)

    if getattr(args, "json", False):
        from snodo.cli.json_output import emit_json, schema_name

        entries = []
        for name in names:
            path = worktree_path(project_root, name)
            try:
                age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                    path.stat().st_mtime, tz=timezone.utc
                )
                age_days = int(age.total_seconds() // 86400)
            except OSError:
                age_days = None
            entries.append({
                "task_id": name,
                "path": str(path),
                "age_days": age_days,
            })
        return emit_json({
            "schema": schema_name("worktree"),
            "ok": True,
            "project_root": project_root,
            "worktrees": entries,
        })

    if not names:
        print("No retained worktrees.")
        return 0

    print("Retained worktrees:")
    for name in names:
        path = worktree_path(project_root, name)
        try:
            age = datetime.now(timezone.utc) - datetime.fromtimestamp(
                path.stat().st_mtime, tz=timezone.utc
            )
            age_str = f"{int(age.total_seconds() // 86400)}d ago"
        except OSError:
            age_str = "?"
        print(f"  {name}  ({age_str})")
        print(f"    remove: snodo worktree remove {name}")
    return 0


def worktree_remove_command(args) -> int:
    task_id = getattr(args, "task_id", "")
    if not task_id:
        print("Usage: snodo worktree remove <task_id>", file=sys.stderr)
        return 1

    project_root = require_project_root()

    # First remove the worktree directory and unregister it from git
    remove_worktree(project_root, task_id)

    # Now delete the task branch(es) — git won't refuse now that the worktree is gone
    try:
        from snodo.tools.git import GitMCP
        git = GitMCP(project_root)
        branch_prefix = f"task/{task_id}"
        for head in list(git.repo.heads):
            if head.name == branch_prefix or head.name.startswith(f"{branch_prefix}/"):
                git.repo.git.branch("-D", head.name)
    except Exception as e:
        _logger.warning("Could not delete task branch for %s: %s", task_id, e)

    print(f"Removed worktree for {task_id}.")
    return 0


def worktree_prune_command(args) -> int:
    project_root = require_project_root()
    days = getattr(args, "days", None)
    force = getattr(args, "force", False)
    dry_run = getattr(args, "dry_run", False)

    if days is None:
        days = _default_ttl_days(project_root)

    # Guard: --days 0 or negative requires --force
    if days <= 0 and not force:
        print(
            "Error: --days must be at least 1 day. A zero threshold would destroy retained evidence. "
            "To remove a specific worktree, use 'snodo worktree remove <task_id>' or pass --force.",
            file=sys.stderr,
        )
        return 1

    names = list_worktrees(project_root)
    if not names:
        print("No retained worktrees.")
        return 0

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale = []
    skipped_failure = []

    for name in names:
        path = worktree_path(project_root, name)
        try:
            mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            if not force and _has_failure_context(project_root, name):
                skipped_failure.append(name)
            else:
                stale.append(name)

    if skipped_failure:
        for name in skipped_failure:
            print(
                f"Skipping {name}: retained for failure evidence "
                f"(use 'snodo worktree remove {name}' or --force to remove)"
            )

    if not stale:
        print(f"No retained worktrees older than {days} days.")
        return 0

    if dry_run:
        print(f"[dry-run] Would remove {len(stale)} retained worktree(s) older than {days} days:")
        for name in stale:
            print(f"  {name}")
        return 0

    if not force:
        print(f"Found {len(stale)} retained worktree(s) older than {days} days:")
        for name in stale:
            print(f"  {name}")
        try:
            answer = input(f"Delete these {len(stale)} worktree(s)? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return 1
        if answer != "y":
            print("Aborted.")
            return 0

    print(f"Removing {len(stale)} retained worktree(s) older than {days} days:")
    for name in stale:
        print(f"  {name}")
        worktree_remove_command(SimpleNamespace(task_id=name))
    return 0
