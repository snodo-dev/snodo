"""Session command - Manage protocol execution sessions.

FILE: snodo/cli/commands/session_cmd.py
"""

import sys

from snodo.infrastructure.session import SessionManager


def session_command(args) -> int:
    """Manage protocol execution sessions."""
    audit_log = getattr(args, "audit_log", None)
    sessions_dir = getattr(args, "sessions_dir", None)
    mgr = SessionManager(audit_log=audit_log, sessions_dir=sessions_dir)

    action = args.session_action
    if action == "list":
        return _session_list(mgr, args)
    elif action == "show":
        return _session_show(mgr, args.session_id)
    elif action == "delete":
        return _session_delete(mgr, args)
    elif action == "prune":
        return _session_prune(mgr, args)
    elif action == "switch":
        return _session_switch(mgr, args)
    elif action == "new":
        return _session_new(mgr, args)
    else:
        print("Unknown session action. Use: list, show, delete, prune, switch, new",
              file=sys.stderr)
        return 1


def _session_list(mgr: SessionManager, args) -> int:
    """List sessions with optional filters."""
    mode = getattr(args, "mode", None)
    project = getattr(args, "project", None)

    sessions = mgr.list_sessions(mode=mode, project_root=project)
    if not sessions:
        print("No sessions found.")
        return 0

    print("Sessions:")
    for s in sessions:
        task = s.checkpoint.current_task or "-"
        print(f"  {s.session_id}  {s.mode:<10}  updated={s.updated_at[:19]}  task={task}")
    return 0


def _session_show(mgr: SessionManager, session_id: str) -> int:
    """Show session details."""
    try:
        session = mgr.load_session(session_id)
    except FileNotFoundError:
        print(f"Error: Session not found: {session_id}", file=sys.stderr)
        return 1

    print(f"Session:  {session.session_id}")
    print(f"Mode:     {session.mode}")
    print(f"Project:  {session.project_root}")
    print(f"Created:  {session.created_at}")
    print(f"Updated:  {session.updated_at}")
    print()
    print("Checkpoint:")
    print(f"  Task:     {session.checkpoint.current_task or '-'}")
    print(f"  Decisions: {session.checkpoint.decisions or '{}'}")
    summary = session.checkpoint.memory_summary
    if summary:
        preview = summary[:80] + "..." if len(summary) > 80 else summary
        print(f"  Memory:   {preview}")
    return 0


def _session_delete(mgr: SessionManager, args) -> int:
    """Delete a session.  Clears active pointer if this was the active session."""
    try:
        mgr.delete_session(args.session_id)
        print(f"Deleted session: {args.session_id}")
        return 0
    except FileNotFoundError:
        print(f"Error: Session not found: {args.session_id}", file=sys.stderr)
        return 1


def _session_prune(mgr: SessionManager, args) -> int:
    """Prune stale sessions."""
    from snodo.cli.config import ConfigManager
    config_mgr = ConfigManager()
    max_age = config_mgr.get_engine_value("max_session_age_days", 30)

    count = mgr.prune_stale(max_age_days=max_age)
    print(f"Pruned {count} stale session(s) (max age: {max_age} days)")
    return 0


def _session_switch(mgr: SessionManager, args) -> int:
    """Set an existing session as the active one for its (project, mode)."""
    from snodo.infrastructure.paths import require_project_root
    project_root = require_project_root()

    try:
        session = mgr.load_session(args.session_id)
    except FileNotFoundError:
        print(f"Error: Session not found: {args.session_id}", file=sys.stderr)
        return 1

    try:
        mgr.set_active_session(project_root, session.mode, session.session_id)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    print(f"Switched to session: {session.session_id} (mode={session.mode})")
    return 0


def _session_new(mgr: SessionManager, args) -> int:
    """Create a new session and set it active."""
    from snodo.infrastructure.state import read_state
    from snodo.infrastructure.paths import require_project_root
    project_root = require_project_root()
    state = read_state(project_root)

    mode = getattr(args, "mode", None) or state.current_mode
    if not mode:
        print("Error: No mode specified. Use --mode or set current_mode via 'snodo mode change'.", file=sys.stderr)
        return 1

    session = mgr.create_session(mode, project_root)
    print(f"Created new session: {session.session_id} (mode={mode})")
    return 0
