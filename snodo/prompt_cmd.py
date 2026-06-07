"""Snodo shell-prompt status — lightweight, import-cheap.

FILE: snodo/prompt_cmd.py

A standalone entry point for shell prompt integration, like git
showing the branch.  Imports ONLY infrastructure/paths.py +
infrastructure/state.py — nothing from compiler/, engine/,
cli.commands, coders, or langchain.  Must run in under 50ms so
it can be called on every shell redraw.

Output format: ``mode`` or ``mode:short_session`` (no ANSI/color).
"""

import sys


def main() -> None:
    """Print current mode + active session short-id, or nothing.

    Silently degrades on any failure — a prompt segment must never
    error into the shell prompt.
    """
    try:
        from snodo.infrastructure.paths import resolve_project_root
    except Exception:
        return

    project_root = resolve_project_root()
    if project_root is None:
        return

    try:
        from snodo.infrastructure.state import read_state
        state = read_state(project_root)
    except Exception:
        return

    mode = state.current_mode
    if not mode:
        return

    session_id = state.active_session.get(mode, "") if state.active_session else ""
    if session_id:
        short = session_id[-6:] if len(session_id) >= 6 else session_id
        print(f"{mode}:{short}")
    else:
        print(mode)


if __name__ == "__main__":
    main()
