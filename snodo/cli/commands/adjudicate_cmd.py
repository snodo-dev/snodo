"""CLI adjudicate command — mint a signed DecisionRecord.

FILE: snodo/cli/commands/adjudicate_cmd.py

Human-only: this command is NOT exposed as an MCP tool.  An LLM
orchestrator cannot mint its own override — the human must run this
CLI command to adjudicate an escalated warn.

Usage:
    snodo adjudicate <session_id> <task_id> <validator_id> \
        --decision proceed|halt --justification "..."
"""

import sys


def adjudicate_command(args) -> int:
    """Mint a signed DecisionRecord and persist it in the session.

    Args:
        args: Namespace with session_id, task_id, validator_id,
              decision, justification, resolved_by.
    """
    session_id = getattr(args, "session_id", "")
    task_id = getattr(args, "task_id", "")
    validator_id = getattr(args, "validator_id", "")
    decision = getattr(args, "decision", "")
    justification = getattr(args, "justification", "")
    resolved_by = getattr(args, "resolved_by", "human")

    if not session_id or not task_id or not validator_id:
        print("Error: session_id, task_id, and validator_id are required", file=sys.stderr)
        return 1
    if decision not in ("proceed", "halt"):
        print("Error: --decision must be 'proceed' or 'halt'", file=sys.stderr)
        return 1
    if not justification:
        print("Error: --justification is required", file=sys.stderr)
        return 1

    from snodo.infrastructure.decisions import (
        DecisionError,
        DecisionInvalidSeverityError,
        DecisionRecordIssuer,
    )
    from snodo.infrastructure.session import SessionManager
    from snodo.core.interfaces import ValidatorResult

    session_mgr = SessionManager()

    try:
        session = session_mgr.load_session(session_id)
    except FileNotFoundError:
        print(f"Error: Session not found: {session_id}", file=sys.stderr)
        return 1

    # Build a synthetic ValidatorResult from the session's escalation data.
    # We look for the validator's result in pending_disagreement or the
    # latest validation_results stored in the session decisions.
    validator_result = _find_validator_result(session, task_id, validator_id)
    if validator_result is None:
        # Fallback: create a minimal result so the record can still be minted.
        # The adjudicated_justification will be empty but the record is valid.
        validator_result = ValidatorResult(
            validator_id=validator_id,
            severity="warn",
            justification="(concern text not available in session)",
        )

    issuer = DecisionRecordIssuer()

    try:
        record = issuer.issue_record(
            task_ref=task_id,
            validator_id=validator_id,
            validator_result=validator_result,
            decision=decision,
            justification=justification,
            resolved_by=resolved_by,
        )
    except DecisionInvalidSeverityError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except DecisionError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    # Persist the DecisionRecord JWT in the session's decision_records list.
    records = session.checkpoint.decisions.get("decision_records", [])
    if not isinstance(records, list):
        records = []
    records.append(record.jwt)
    session_mgr.update_decision(session_id, "decision_records", records)

    print(f"DecisionRecord minted: {validator_id} on {task_id} → {decision}")
    print(f"  Record ID: {issuer._record_id(record.jwt)}")
    print(f"  Severity:  {record.adjudicated_severity}")
    print(f"  Reason:    {justification}")
    return 0


def _find_validator_result(session, task_id: str, validator_id: str):
    """Find the ValidatorResult for a specific validator on a task.

    Searches session decisions for validation results or pending
    disagreements that match the task and validator.
    """
    from snodo.core.interfaces import ValidatorResult

    # Check pending_disagreement in the latest resolution data
    decisions = session.checkpoint.decisions
    for key, value in decisions.items():
        if isinstance(value, dict):
            pending = value.get("pending_disagreement")
            if pending and isinstance(pending, dict):
                for vr in pending.get("validator_results", []):
                    if vr.get("validator_id") == validator_id:
                        return ValidatorResult(
                            validator_id=vr["validator_id"],
                            severity=vr.get("severity", "warn"),
                            justification=vr.get("justification", ""),
                        )

    # Check top-level validation_results in any stored resolution
    for key, value in decisions.items():
        if isinstance(value, dict):
            for vr in value.get("validation_results", []):
                if vr.get("validator_id") == validator_id:
                    return ValidatorResult(
                        validator_id=vr["validator_id"],
                        severity=vr.get("severity", "warn"),
                        justification=vr.get("justification", ""),
                    )

    return None
