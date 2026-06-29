"""Generate prose instructions from a Protocol object for arm-b.

The rendered prose is the methodology content that arm-b receives as
system/context.  Arm-c enforces the same protocol object programmatically.
The parity gate asserts that the prose generated here matches the protocol
arm-c uses.
"""

from __future__ import annotations

from snodo.compiler.models import Protocol


def protocol_to_prose(protocol: Protocol) -> str:
    """Render a Protocol object as human-readable prose instructions.

    The output describes modes, tools, validators, constraints, and
    transitions so an LLM can follow the methodology without runtime
    enforcement.
    """
    lines: list[str] = []
    lines.append(f"# Protocol: {protocol.name} (id={protocol.protocol_id})")
    lines.append(f"Disagreement policy: {protocol.disagreement_policy.value}")
    lines.append("")

    # Execution config
    ex = protocol.execution
    lines.append("## Execution Configuration")
    lines.append(f"- Branch prefix: {ex.branch_prefix}")
    lines.append(f"- Branch TTL (days): {ex.branch_ttl_days}")
    lines.append(f"- Max retries: {ex.max_retries}")
    lines.append(f"- Max recovery depth: {ex.max_recovery_depth}")
    lines.append(f"- Max total fix attempts: {ex.max_total_fix_attempts}")
    lines.append("")

    # Modes
    lines.append(f"## Modes ({len(protocol.modes)})")
    for m in protocol.modes:
        lines.append(f"### {m.name} (id={m.mode_id})")
        if m.tools:
            lines.append(f"Tools: {', '.join(sorted(m.tools))}")
        if m.validators:
            lines.append(f"Validators: {', '.join(m.validators)}")
        if m.transitions:
            for trigger, target in m.transitions.items():
                lines.append(f"Transition: on '{trigger}' -> {target}")
        if m.constraints:
            for c in m.constraints:
                lines.append(f"Constraint: {c.description}")
        lines.append("")

    # Global constraints
    if protocol.global_constraints:
        lines.append("## Global Constraints")
        for c in protocol.global_constraints:
            lines.append(f"- {c.description} (severity: {c.severity})")
        lines.append("")

    # Validators
    lines.append(f"## Validators ({len(protocol.validators)})")
    for v in protocol.validators:
        phase_label = "pre-execution" if v.evaluation_phase == "pre_execute" else "post-execution"
        lines.append(f"### {v.validator_id} (type={v.validator_type}, phase={phase_label})")
        if v.criteria:
            for crit in v.criteria:
                lines.append(f"  - {crit}")
        if v.severity_cap:
            lines.append(f"  Severity cap: {v.severity_cap.value}")
        lines.append("")

    return "\n".join(lines)
