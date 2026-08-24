"""Status command — answer "where am I and what just happened".

FILE: snodo/cli/commands/status_cmd.py

Reads only what is already on disk: .snodo/protocol.yml (protocol id),
.snodo/state.json (active mode + active session), and the session store
(most recent run + outcome). Nothing new is recorded.
"""

from pathlib import Path
from types import SimpleNamespace

import typer


def register(app: typer.Typer) -> None:
    """Register top-level CLI commands onto app (called by discovery loop)."""

    @app.command()
    def status():
        """Show protocol, active mode, active session, and most recent run."""
        return status_command(SimpleNamespace())


def status_command(args) -> int:
    """Show protocol, active mode, active session, and most recent run."""
    from snodo.infrastructure.paths import require_project_root
    from snodo.infrastructure.state import read_state
    from snodo.infrastructure.session import SessionManager

    project_root = require_project_root()
    state = read_state(project_root)

    protocol_id, protocol_name = _read_protocol(project_root)
    print(f"Protocol: {protocol_name} ({protocol_id})" if protocol_name else f"Protocol: {protocol_id}")

    mode = state.current_mode or "(none)"
    print(f"Mode:     {mode}")

    active_session = state.active_session.get(mode) if mode != "(none)" else None
    if active_session:
        print(f"Session:  {active_session}")
    else:
        print("Session:  (none)")

    _print_most_recent(project_root, SessionManager())

    print()
    print("Inspect:")
    if active_session:
        print(f"  snodo session show {active_session}")
    print("  snodo session list")
    print("  snodo mode show")
    return 0


def _read_protocol(project_root: str) -> tuple:
    """Return (protocol_id, protocol_name) from .snodo/protocol.yml."""
    protocol_path = Path(project_root) / ".snodo" / "protocol.yml"
    if not protocol_path.exists():
        return "(no protocol.yml)", ""
    try:
        import yaml
        data = yaml.safe_load(protocol_path.read_text())
    except Exception:
        return "(unreadable)", ""
    if not isinstance(data, dict):
        return "(unreadable)", ""
    return data.get("protocol_id", "(unknown)"), data.get("name", "")


def _print_most_recent(project_root: str, mgr) -> None:
    """Print the most recent run (session) and its outcome for this project."""
    sessions = mgr.list_sessions(project_root=project_root)
    if not sessions:
        print("Last run: (none)")
        return

    sessions.sort(key=lambda s: s.updated_at, reverse=True)
    recent = sessions[0]
    outcome = _session_outcome(recent)
    print(f"Last run: {recent.session_id}  mode={recent.mode}  updated={recent.updated_at[:19]}  outcome={outcome}")


def _session_outcome(session) -> str:
    """Derive a compact outcome string from a session checkpoint."""
    halt = session.checkpoint.decisions.get("halt", {})
    if isinstance(halt, dict) and halt:
        decisions = [h.get("final_decision", "unknown") for h in halt.values() if isinstance(h, dict)]
        if decisions:
            return decisions[-1]
    failures = session.checkpoint.decisions.get("task_failure", {})
    if isinstance(failures, dict) and failures:
        return "failed"
    if session.checkpoint.current_task:
        return "in progress"
    return "no tasks"
