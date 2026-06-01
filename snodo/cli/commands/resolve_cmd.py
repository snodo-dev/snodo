"""CLI resolve command — resolve escalated disagreements.

FILE: snodo/cli/commands/resolve_cmd.py (Task 7.12)

Provides the snodo resolve CLI surface for disagreement resolution,
complementing the resolve_disagreement MCP tool from 7.10.
"""

import sys


def resolve_command(args) -> int:
    """Store a disagreement resolution in session decisions.

    Usage:
        snodo resolve <session_id> <task_id> --decision proceed|halt
                --justification "<text>" [--resolved-by "<text>"]
    """
    session_id = getattr(args, "session_id", "")
    task_id = getattr(args, "task_id", "")
    decision = getattr(args, "decision", "")
    justification = getattr(args, "justification", "")
    resolved_by = getattr(args, "resolved_by", "cli")

    if not session_id or not task_id:
        print("Error: session_id and task_id are required", file=sys.stderr)
        return 1
    if decision not in ("proceed", "halt"):
        print("Error: --decision must be 'proceed' or 'halt'", file=sys.stderr)
        return 1
    if not justification:
        print("Error: --justification is required", file=sys.stderr)
        return 1

    from snodo.mcp.resolution import apply_resolution

    try:
        result = apply_resolution(
            task_id=task_id,
            session_id=session_id,
            resolution=decision,
            justification=justification,
            resolved_by=resolved_by,
        )
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError:
        print(f"Error: Session not found: {session_id}", file=sys.stderr)
        return 1

    print(f"Resolution applied: {result['resolution']}")
    return 0
