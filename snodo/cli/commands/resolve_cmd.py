"""CLI resolve command — backward-compat wrapper for adjudicate.

FILE: snodo/cli/commands/resolve_cmd.py

Deprecated: use `snodo adjudicate <session_id> <task_id> <validator_id>`
instead.  This command still works but requires the user to specify
which validator's concern to adjudicate.  If no validator_id is given,
it lists the escalated validators and asks the user to pick one.
"""

import sys


def resolve_command(args) -> int:
    """Backward-compat: delegate to DecisionRecord-based adjudication.

    Usage (deprecated):
        snodo resolve <session_id> <task_id> --decision proceed|halt \
                --justification "<text>" [--resolved-by "<text>"]

    Prefer:
        snodo adjudicate <session_id> <task_id> <validator_id> \
                --decision proceed|halt --justification "<text>"
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

    # Check if validator_id is provided (new-style call)
    validator_id = getattr(args, "validator_id", None)

    if validator_id:
        # New-style: direct adjudicate
        from snodo.cli.commands.adjudicate_cmd import adjudicate_command
        new_args = type(args)(
            session_id=session_id,
            task_id=task_id,
            validator_id=validator_id,
            decision=decision,
            justification=justification,
            resolved_by=resolved_by or "human",
        )
        return adjudicate_command(new_args)

    # Old-style: no validator_id — find escalated validators from session
    from snodo.infrastructure.session import SessionManager

    session_mgr = SessionManager()
    try:
        session = session_mgr.load_session(session_id)
    except FileNotFoundError:
        print(f"Error: Session not found: {session_id}", file=sys.stderr)
        return 1

    # Find escalated validators for this task
    escalated = _find_escalated_validators(session, task_id)
    if not escalated:
        print(f"Error: No escalated validators found for task {task_id}", file=sys.stderr)
        return 1

    if len(escalated) == 1:
        # Single escalated validator — auto-adjudicate
        validator_id = escalated[0]["validator_id"]
        print(f"Adjudicating single escalated validator: {validator_id}")
    else:
        # Multiple escalated validators — list them and require explicit choice
        print(f"Multiple validators escalated for task {task_id}:")
        for v in escalated:
            print(f"  - {v['validator_id']}: {v['severity']} — {v['justification'][:80]}")
        print()
        print("Use `snodo adjudicate` to specify which validator to adjudicate:")
        print(f"  snodo adjudicate {session_id} {task_id} <validator_id> "
              f"--decision {decision} --justification \"{justification}\"")
        return 1

    # Proceed with single-validator adjudication
    from snodo.cli.commands.adjudicate_cmd import adjudicate_command
    new_args = type(args)(
        session_id=session_id,
        task_id=task_id,
        validator_id=validator_id,
        decision=decision,
        justification=justification,
        resolved_by=resolved_by or "human",
    )
    return adjudicate_command(new_args)


def _find_escalated_validators(session, task_id: str):
    """Find validators that escalated for a given task."""
    decisions = session.checkpoint.decisions
    escalated = []

    for key, value in decisions.items():
        if not isinstance(value, dict):
            continue
        pending = value.get("pending_disagreement")
        if pending and isinstance(pending, dict):
            for vr in pending.get("validator_results", []):
                severity = vr.get("severity", "")
                if severity in ("warn", "error"):
                    escalated.append({
                        "validator_id": vr["validator_id"],
                        "severity": severity,
                        "justification": vr.get("justification", ""),
                    })

    # Deduplicate by validator_id
    seen = set()
    unique = []
    for v in escalated:
        if v["validator_id"] not in seen:
            seen.add(v["validator_id"])
            unique.append(v)

    return unique
