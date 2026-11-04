"""Pure classification of persisted job/task outcomes for operator display."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskOutcome:
    """Operator-facing situation inferred from existing task data."""

    situation: str
    recovery_task_id: str | None
    halt_reason: Any


def classify_outcome(state: Any, audit_events: Any) -> TaskOutcome:
    """Classify a completed task without changing or persisting its state.

    Malformed input is treated conservatively: only an explicit clean exit is
    success; otherwise the task is considered blocked.
    """
    state = state if isinstance(state, dict) else {}
    halt = state.get("halt")
    halt = halt if isinstance(halt, dict) else {}
    reason = halt.get("reason")
    halt_type = halt.get("type", halt.get("halt_type"))
    events = audit_events if isinstance(audit_events, (list, tuple)) else ()

    recovery_id = None
    pending_escalation = False
    exhausted_or_stalled = False
    for event in events:
        if not isinstance(event, dict):
            continue
        kind = event.get("event_type", event.get("op"))
        if kind == "subtask_spawned":
            candidate = event.get("task_ref", event.get("recovery_task_id"))
            if isinstance(candidate, str) and candidate:
                recovery_id = candidate
        elif kind == "disagreement_escalated":
            pending_escalation = True
        elif kind in {"recovery_exhausted", "recovery_stalled"}:
            exhausted_or_stalled = True

    if halt_type in {"validator_error", "internal_error", "environment_error"}:
        situation = "engine_fault"
    elif pending_escalation and halt.get("adjudicable") is True:
        situation = "human_decision"
    elif exhausted_or_stalled:
        situation = "blocked"
    elif recovery_id and state.get("status") in {"completed", "finished"}:
        situation = "warn_recovered"
    elif halt:
        situation = "blocked"
    elif state.get("status") in {"completed", "finished"} and state.get("exit_code") == 0:
        situation = "success"
    elif state.get("exit_code") == 0 and state.get("status") not in {"failed", "halted"}:
        situation = "success"
    else:
        situation = "blocked"

    return TaskOutcome(situation, recovery_id, reason)
