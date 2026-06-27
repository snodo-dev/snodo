"""Shared resolution helper — used by both MCP and CLI surfaces.

FILE: snodo/mcp/resolution.py (Task 7.12)

Extracted from ProtocolMCPServer._handle_resolve_disagreement so that
the snodo resolve CLI command produces identical session state.
"""

from datetime import datetime, timezone
from typing import Any, Dict


def apply_resolution(
    task_id: str,
    session_id: str,
    resolution: str,
    justification: str,
    resolved_by: str = "orchestrator",
    *,
    audit_log: Any = None,
) -> Dict[str, Any]:
    """Store a disagreement resolution in session decisions.

    Args:
        task_id: Task ID whose disagreement is being resolved.
        session_id: Session ID.
        resolution: "proceed" or "halt".
        justification: Human-readable justification.
        resolved_by: Who resolved ("human", "orchestrator", "cli").
        audit_log: Optional AuditLog for INV4 event logging.

    Returns:
        Dict with status, resolution, session_id, task_id.

    Raises:
        ValueError: If resolution is invalid.
        FileNotFoundError: If session does not exist.
    """
    if resolution not in ("proceed", "halt"):
        raise ValueError(f"Resolution must be 'proceed' or 'halt', got {resolution!r}")

    from snodo.infrastructure.session import SessionManager
    session_mgr = SessionManager(audit_log=audit_log)

    # Validate session exists
    session_mgr.load_session(session_id)

    resolution_data = {
        "resolution": resolution,
        "justification": justification,
        "resolved_by": resolved_by,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    session_mgr.update_decision(
        session_id, f"resolution_{task_id}", resolution_data,
    )

    if audit_log is not None:
        audit_log.append_event("disagreement_resolved", {
            "op": "disagreement_resolved",
            "task_ref": task_id,
            "session_id": session_id,
            "resolution": resolution,
            "justification": justification,
            "resolved_by": resolved_by,
        })

    return {
        "status": "resolved",
        "resolution": resolution,
        "session_id": session_id,
        "task_id": task_id,
    }
