"""Task command — snodo task list / abandon / prune.

FILE: snodo/cli/commands/task_cmd.py
"""

import sys
from types import SimpleNamespace
from typing import Optional

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
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Inspect a task's halt and failure record from the active session."""
    return task_show_command(SimpleNamespace(task_id=task_id, json=json))


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


@app.command(name="review")
def task_review(
    task_id: Optional[str] = typer.Argument(None, help="Task ID or 'report' to view review statistics"),
    verdict: Optional[str] = typer.Argument(None, help="Verdict: accepted (unchanged), amended, or discarded"),
    notes: Optional[str] = typer.Option(None, "--notes", help="Optional review notes"),
    report: bool = typer.Option(False, "--report", help="Report review acceptance statistics"),
    pending: bool = typer.Option(False, "--pending", help="List merged units with no review record"),
    days: int = typer.Option(30, "--days", help="Window in days for review report"),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Record operator review verdict for a completed task, report acceptance rate, or list pending reviews."""
    return task_review_command(SimpleNamespace(
        task_id=task_id, verdict=verdict, notes=notes, report=report, pending=pending, days=days, json=json
    ))


@app.command(name="report")
def task_report(
    days: int = typer.Option(30, "--days", help="Window in days for review report"),
    json: bool = typer.Option(False, "--json", help="Emit machine-readable JSON"),
):
    """Report operator review acceptance rate over a window."""
    return task_report_command(SimpleNamespace(days=days, json=json))



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
    json_out = getattr(args, "json", False)
    if not task_id:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task", "task_id required", 1)
        print("Usage: snodo task show <task_id>", file=sys.stderr)
        return 1

    project_root = resolve_project_root()
    if project_root is None:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task", "Not inside a snodo project.", 1)
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    from snodo.infrastructure.state import read_state
    from snodo.infrastructure.session import SessionManager

    state = read_state(project_root)
    mode = state.current_mode
    if not mode:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task", "No active mode. Run 'snodo mode change <m>' first.", 1)
        print("No active mode. Run 'snodo mode change <m>' first.", file=sys.stderr)
        return 1

    mgr = SessionManager()
    session = mgr.get_active_session(mode, project_root)
    if session is None:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task", f"No active session for mode={mode}.", 1)
        print(f"No active session for mode={mode}.", file=sys.stderr)
        return 1

    decisions = session.checkpoint.decisions or {}
    halt = decisions.get("halt", {})
    failure = decisions.get("task_failure", {})

    halt_entry = halt.get(task_id) if isinstance(halt, dict) else None
    failure_entry = failure.get(task_id) if isinstance(failure, dict) else None

    if not halt_entry and not failure_entry:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task", f"No record for task {task_id} in session {session.session_id}.", 1)
        print(f"No record for task {task_id} in session {session.session_id}.")
        return 1

    if json_out:
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({
            "schema": schema_name("task"),
            "ok": True,
            "task_id": task_id,
            "session_id": session.session_id,
            "mode": session.mode,
            "halt": halt_entry if isinstance(halt_entry, dict) else None,
            "failure": failure_entry if isinstance(failure_entry, dict) else None,
        })

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


VALID_VERDICTS = {"accepted", "amended", "discarded"}


def task_review_command(args) -> int:
    """Record operator review verdict for a task, list pending reviews, or generate report."""
    task_id = getattr(args, "task_id", "") or ""
    verdict_raw = getattr(args, "verdict", None)
    notes = getattr(args, "notes", None) or ""
    report_flag = getattr(args, "report", False)
    pending_flag = getattr(args, "pending", False)
    json_out = getattr(args, "json", False)

    if pending_flag:
        return task_review_pending_command(args)

    if report_flag or task_id.lower() in ("report", "--report"):
        return task_report_command(args)

    if not task_id:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task_review", "task_id is required", 1)
        print("Usage: snodo task review <task_id> <verdict> [--notes NOTES]", file=sys.stderr)
        return 1

    if not verdict_raw:
        valid_str = ", ".join(sorted(VALID_VERDICTS))
        msg = f"A verdict is required. Must be one of: {valid_str}"
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task_review", msg, 1)
        print(f"Error: {msg}", file=sys.stderr)
        print("  accepted  : Task completed and accepted unchanged", file=sys.stderr)
        print("  amended   : Task completed but required manual edits", file=sys.stderr)
        print("  discarded : Task output was rejected or reverted", file=sys.stderr)
        return 1

    if verdict_raw.lower() not in VALID_VERDICTS:
        valid_str = ", ".join(sorted(VALID_VERDICTS))
        msg = f"Invalid verdict '{verdict_raw}'. Must be one of: {valid_str}"
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task_review", msg, 1)
        print(f"Error: {msg}", file=sys.stderr)
        print("  accepted  : Task completed and accepted unchanged", file=sys.stderr)
        print("  amended   : Task completed but required manual edits", file=sys.stderr)
        print("  discarded : Task output was rejected or reverted", file=sys.stderr)
        return 1

    verdict = verdict_raw.lower()
    project_root = resolve_project_root()
    if project_root is None:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task_review", "Not inside a snodo project.", 1)
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    from snodo.infrastructure.audit import get_audit_log
    from datetime import datetime, timezone

    audit_log = get_audit_log()
    if audit_log is None:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task_review", "Audit log unavailable.", 1)
        print("Error: Audit log unavailable.", file=sys.stderr)
        return 1

    audit_log.append_event("human_review_recorded", {
        "op": "human_review_recorded",
        "task_ref": task_id,
        "verdict": verdict,
        "notes": notes,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
    })

    if json_out:
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({
            "schema": schema_name("task_review"),
            "ok": True,
            "task_id": task_id,
            "verdict": verdict,
            "notes": notes,
        })

    print(f"✓ Recorded review verdict '{verdict}' for task {task_id} in audit log.")
    return 0


def _merge_identity(data: dict) -> str:
    """Return the identity of a merged unit from an event's data.

    The merge commit SHA is the identity (Fixes #101): N merges of the same
    worktree branch produce N distinct merge commits, so the report can tell
    them apart. The branch name is a human-readable label that repeats across
    merges and must not be used as an identity. Legacy events recorded before
    the SHA was added fall back to ``task_ref``.
    """
    return data.get("merge_sha") or data.get("task_ref") or data.get("task_id") or ""


def task_report_command(args) -> int:
    """Report operator human review acceptance statistics over a window."""
    from datetime import datetime, timezone, timedelta
    from snodo.infrastructure.audit import get_audit_log

    days = getattr(args, "days", 30) or 30
    json_out = getattr(args, "json", False)

    project_root = resolve_project_root()
    if project_root is None:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task_review_report", "Not inside a snodo project.", 1)
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    audit_log = get_audit_log()
    events = audit_log.events if audit_log else []

    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    # Two distinct denominators (Fixes #101): the engine's completed-task set
    # (task_complete events, keyed on the task id) and the merged-unit set
    # (task_merged events, keyed on the merge commit SHA). They answer
    # different questions and must not be conflated.
    completed_tasks = set()   # task_refs from task_complete
    merged_units = set()       # merge identities from task_merged
    reviews = {}               # merge identity -> verdict

    for ev in events:
        data = ev.data or {}
        # Parse timestamp if present
        ev_time = None
        ts_str = data.get("timestamp") or data.get("recorded_at") or getattr(ev, "timestamp", "")
        if ts_str:
            try:
                ev_time = datetime.fromisoformat(ts_str)
            except Exception:
                pass

        if ev_time and ev_time < cutoff:
            continue

        op = data.get("op") or ev.event_type

        if op == "task_complete":
            task_ref = data.get("task_ref") or data.get("task_id")
            if task_ref:
                completed_tasks.add(task_ref)

        if op == "task_merged":
            identity = _merge_identity(data)
            if identity:
                merged_units.add(identity)

        if op == "human_review_recorded":
            identity = _merge_identity(data)
            if identity and data.get("verdict"):
                reviews[identity] = data["verdict"].lower()

    # A review without a matching task_merged still counts as a merged unit.
    merged_units.update(reviews.keys())
    total_completed = len(completed_tasks)
    total_merged = len(merged_units)

    accepted_count = sum(1 for t in merged_units if reviews.get(t) == "accepted")
    amended_count = sum(1 for t in merged_units if reviews.get(t) == "amended")
    discarded_count = sum(1 for t in merged_units if reviews.get(t) == "discarded")
    reviewed_count = accepted_count + amended_count + discarded_count
    unreviewed_count = max(0, total_merged - reviewed_count)

    rate_pct = (accepted_count / reviewed_count * 100.0) if reviewed_count > 0 else 0.0

    if json_out:
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({
            "schema": schema_name("task_review_report"),
            "ok": True,
            "days_window": days,
            "completed_tasks": total_completed,
            "merged_units": total_merged,
            "total_reviewed": reviewed_count,
            "accepted_unchanged": accepted_count,
            "amended": amended_count,
            "discarded": discarded_count,
            "unreviewed": unreviewed_count,
            "acceptance_rate_pct": round(rate_pct, 1),
        })

    print(f"Human Review Acceptance Rate (Last {days} days)")
    print("-" * 45)
    print(f"Completed tasks (task_complete): {total_completed}")
    print(f"Merged units (task_merged):      {total_merged}")
    rev_pct = (reviewed_count / total_merged * 100.0) if total_merged > 0 else 0.0
    print(f"Reviewed tasks:            {reviewed_count} ({rev_pct:.1f}%)")
    print(f"  - Accepted unchanged:    {accepted_count}")
    print(f"  - Amended by operator:   {amended_count}")
    print(f"  - Discarded / reverted:  {discarded_count}")
    if unreviewed_count > 0:
        print(f"  - Unreviewed:            {unreviewed_count}")
    print()
    print(f"Unchanged Acceptance Rate: {rate_pct:.1f}% ({accepted_count}/{reviewed_count} reviewed tasks accepted unchanged)")
    return 0


def _spec_excerpt(spec: str, max_chars: int = 80) -> str:
    """Return a one-line excerpt of *spec* for the pending list."""
    if not spec:
        return ""
    one_line = " ".join(spec.split())
    if len(one_line) <= max_chars:
        return one_line
    return one_line[: max_chars - 1] + "…"


def task_review_pending_command(args) -> int:
    """List every merged unit with no review record, newest first.

    Read-only: reads the audit log (task_merged / human_review_recorded) and
    the session halt payloads for the spec excerpt. Never creates, mutates or
    clears any review record.
    """
    from datetime import datetime

    from snodo.infrastructure.audit import get_audit_log

    json_out = getattr(args, "json", False)

    project_root = resolve_project_root()
    if project_root is None:
        if json_out:
            from snodo.cli.json_output import emit_error
            return emit_error("task_review_pending", "Not inside a snodo project.", 1)
        print("Not inside a snodo project.", file=sys.stderr)
        return 1

    audit_log = get_audit_log()
    events = audit_log.events if audit_log else []

    # Merge identity -> (task_ref, branch, merge timestamp). The merge commit
    # SHA is the identity (Fixes #101); the branch name is a human label that
    # repeats across merges and must not be used as an identity.
    merged: dict = {}
    for ev in events:
        data = ev.data or {}
        op = data.get("op") or ev.event_type
        if op != "task_merged":
            continue
        identity = _merge_identity(data)
        if not identity:
            continue
        ts_str = data.get("timestamp") or getattr(ev, "timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str) if ts_str else None
        except (ValueError, TypeError):
            ts = None
        merged[identity] = {
            "task_ref": data.get("task_ref") or data.get("task_id") or identity,
            "branch": data.get("branch", ""),
            "merge_ts": ts,
        }

    # Reviewed identities: any human_review_recorded with a verdict.
    reviewed: set = set()
    for ev in events:
        data = ev.data or {}
        op = data.get("op") or ev.event_type
        if op != "human_review_recorded":
            continue
        identity = _merge_identity(data)
        if identity and data.get("verdict"):
            reviewed.add(identity)

    # Spec excerpt from the session halt payloads (checkpoint.decisions.halt).
    specs: dict = {}
    try:
        from snodo.infrastructure.state import read_state
        from snodo.infrastructure.session import SessionManager

        state = read_state(project_root)
        mode = state.current_mode
        if mode:
            mgr = SessionManager()
            session = mgr.get_active_session(mode, project_root)
            if session:
                halt = session.checkpoint.decisions.get("halt", {})
                if isinstance(halt, dict):
                    for tid, payload in halt.items():
                        if isinstance(payload, dict) and payload.get("task_spec"):
                            specs[tid] = payload["task_spec"]
    except Exception:
        pass  # Spec excerpt is best-effort; the pending list still works.

    pending = []
    for identity, info in merged.items():
        if identity in reviewed:
            continue
        task_ref = info["task_ref"]
        pending.append({
            "unit_id": identity,
            "task_id": task_ref,
            "branch": info["branch"],
            "merge_timestamp": info["merge_ts"].isoformat() if info["merge_ts"] else "",
            "spec_excerpt": _spec_excerpt(specs.get(task_ref, "")),
        })

    # Newest first; units without a parseable timestamp sort last.
    pending.sort(key=lambda r: r["merge_timestamp"], reverse=True)

    if json_out:
        from snodo.cli.json_output import emit_json, schema_name
        return emit_json({
            "schema": schema_name("task_review_pending"),
            "ok": True,
            "count": len(pending),
            "pending": pending,
        })

    if not pending:
        print("No merged units awaiting review.")
        return 0

    print(f"{len(pending)} merged unit(s) awaiting review:")
    print()
    for r in pending:
        ts = r["merge_timestamp"] or "unknown"
        print(f"  {r['unit_id']}  {r['branch'] or '—'}  merged {ts}")
        print(f"    task: {r['task_id']}")
        if r["spec_excerpt"]:
            print(f"    spec: {r['spec_excerpt']}")
    print()
    print("Review: snodo task review <unit_id> <verdict>")
    return 0
