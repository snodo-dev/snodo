"""Mode command — manage the active protocol mode.

FILE: snodo/cli/commands/mode_cmd.py (Task 7.19)
"""

import sys
from pathlib import Path

from snodo.infrastructure.state import read_state, write_state


def mode_command(args) -> int:
    """Manage active protocol mode."""
    project_root = str(Path.cwd())
    state = read_state(project_root)
    action = getattr(args, "mode_action", "show")

    if action == "show":
        return _mode_show(state)
    elif action == "change":
        return _mode_change(args, state, project_root)
    else:
        print("Unknown mode action. Use: show, change", file=sys.stderr)
        return 1


def _mode_show(state) -> int:
    """Display the current active mode."""
    if not state.current_mode:
        print("No mode set. Run 'snodo mode change <m>' to select one.")
        return 0

    # Try to load protocol for richer display
    protocol_path = Path(".snodo/protocol.yml")
    mode_name = state.current_mode
    if protocol_path.exists():
        import yaml
        from snodo.compiler.models import Protocol
        try:
            data = yaml.safe_load(protocol_path.read_text())
            protocol = Protocol(**data)
            mode = protocol.get_mode(state.current_mode)
            if mode:
                mode_name = f"{mode.name} ({state.current_mode})"
        except Exception:
            pass

    print(f"Current mode: {mode_name}")
    if state.active_session:
        print(f"Active session: {state.active_session}")
    return 0


def _mode_change(args, state, project_root) -> int:
    """Change the active mode and optionally select a session."""
    new_mode = getattr(args, "new_mode", "")
    if not new_mode:
        print("Error: mode name required", file=sys.stderr)
        return 1

    # Validate mode exists in protocol
    protocol_path = Path(".snodo/protocol.yml")
    if not protocol_path.exists():
        print("Error: .snodo/protocol.yml not found. Run 'snodo init' first.",
              file=sys.stderr)
        return 1

    import yaml
    from snodo.compiler.models import Protocol
    from snodo.infrastructure.session import SessionManager
    from snodo.infrastructure.paths import resolve_home

    try:
        data = yaml.safe_load(protocol_path.read_text())
        protocol = Protocol(**data)
    except Exception as e:
        print(f"Error loading protocol: {e}", file=sys.stderr)
        return 1

    mode = protocol.get_mode(new_mode)
    if not mode:
        available = ", ".join(m.mode_id for m in protocol.modes)
        print(f"Error: Mode '{new_mode}' not found. Available: {available}",
              file=sys.stderr)
        return 1

    # Update state
    state.current_mode = new_mode
    write_state(project_root, state)

    print(f"Mode changed to: {mode.name} ({new_mode})")

    # Session picker: list sessions for the new mode
    session_mgr = SessionManager(sessions_dir=resolve_home() / "sessions")
    sessions = session_mgr.list_sessions(mode=new_mode, project_root=project_root)
    if sessions:
        print()
        print(f"Available sessions for {new_mode}:")
        for s in sessions:
            task = s.checkpoint.current_task or "(none)"
            print(f"  {s.session_id}  updated={s.updated_at[:19]}  task={task}")
        print()
        print("Use 'snodo run' to continue with the most recent session,")
        print("or 'snodo run --resume <session_id>' for a specific one.")
    else:
        print("No existing sessions for this mode. Next 'snodo run' will create one.")
    return 0
