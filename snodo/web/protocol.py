"""Build the dashboard-safe view of a project's validated protocol."""

from __future__ import annotations

import hashlib
from typing import Any

from snodo.mcp.schema import build_protocol_schema_publication
from snodo.protocols import load_protocol
from snodo.protocol_validation import validate_protocol


def protocol_view(project: Any) -> dict[str, Any]:
    """Return protocol details, or a non-sensitive error suitable for JSON."""
    path = project.path / ".snodo" / "protocol.yml"
    try:
        content = path.read_bytes()
    except OSError:
        return {"error": "Protocol file not found or could not be read."}
    findings = validate_protocol(path)
    if findings:
        return {"error": "Protocol file is invalid."}
    protocol = load_protocol(path)
    if protocol is None:
        return {"error": "Protocol file is invalid."}

    modes = [
        {"id": mode.mode_id, "name": mode.name, "tools": mode.tools,
         "concurrency": mode.concurrency, "validators": mode.validators}
        for mode in protocol.modes
    ]
    memberships = {validator.validator_id: [] for validator in protocol.validators}
    for mode in protocol.modes:
        for validator_id in mode.validators:
            if validator_id in memberships:
                memberships[validator_id].append(mode.mode_id)
    validators = [
        {"id": validator.validator_id, "type": validator.validator_type,
         "phase": validator.evaluation_phase, "scope": validator.scope,
         "severity_cap": validator.severity_cap.value if validator.severity_cap else None,
         "tools": validator.tools, "check_tool_access": validator.check_tool_access,
         "criteria": validator.criteria, "modes": memberships[validator.validator_id]}
        for validator in protocol.validators
    ]
    execution = protocol.execution
    publication = build_protocol_schema_publication()
    return {
        "project": project.name, "path": str(path),
        "etag": hashlib.sha256(content).hexdigest(),
        "protocol": {"id": protocol.protocol_id, "name": protocol.name,
                     "version": protocol.version, "initial_mode": protocol.initial_mode,
                     "effective_delivery": protocol.delivery_for(protocol.initial_mode),
                      "disagreement_policy": protocol.disagreement_policy.value},
        "modes": modes, "validators": validators,
        "execution": {
            "max_retries": execution.max_retries, "branch_ttl_days": execution.branch_ttl_days,
            "max_recovery_depth": execution.max_recovery_depth,
            "max_total_fix_attempts": execution.max_total_fix_attempts,
            "branch_prefix": execution.branch_prefix, "delivery": execution.delivery,
            "auto_merge": execution.auto_merge, "parallel_runs": protocol.queue.parallel_runs,
            "non_blocking": protocol.queue.non_blocking,
        },
        "rules": [{"id": rule["id"], "description": rule["description"], "ok": True}
                  for rule in publication["x-snodo-cross-field-rules"]],
    }
