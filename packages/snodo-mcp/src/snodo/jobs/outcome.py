"""Pure presentation projection of task data into operator situations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class TaskOutcome:
    """Operator-facing situation inferred from existing task data."""

    situation: str
    recovery_task_id: str | None
    halt_reason: str | None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def classify_outcome(state: Any, audit_events: Any) -> TaskOutcome:
    """Project a task state and audit events into an operator situation.

    This is presentation-only: it reads existing inputs and never persists or
    changes task state. Unknown and malformed data fails closed to ``blocked``.
    """
    state = _mapping(state)
    halt_value = state.get("halt")
    halt = _mapping(halt_value)
    reason = halt.get("reason")
    reason = reason if isinstance(reason, str) else None
    halt_type = halt.get("type")
    if not isinstance(halt_type, str):
        halt_type = halt.get("halt_type")
    if not isinstance(halt_type, str):
        halt_type = None
    status = state.get("status")
    status = status if isinstance(status, str) else None
    exit_code = state.get("exit_code")
    exit_code = exit_code if type(exit_code) is int else None

    recovery_id = None
    pending_escalation = False
    exhausted_or_stalled = False
    events = audit_events if isinstance(audit_events, (list, tuple)) else ()
    for event_value in events:
        event = _mapping(event_value)
        if not event:
            continue
        kind = event.get("event_type")
        if not isinstance(kind, str):
            kind = event.get("op")
        if not isinstance(kind, str):
            continue
        if kind == "subtask_spawned":
            candidate = event.get("task_ref")
            if not isinstance(candidate, str):
                candidate = event.get("recovery_task_id")
            if isinstance(candidate, str) and candidate:
                recovery_id = candidate
        elif kind == "disagreement_escalated":
            pending_escalation = True
        elif kind in ("recovery_exhausted", "recovery_stalled"):
            exhausted_or_stalled = True

    if halt_type in ("validator_error", "internal_error", "environment_error"):
        situation = "engine_fault"
    elif pending_escalation and halt.get("adjudicable") is True:
        situation = "human_decision"
    elif exhausted_or_stalled:
        situation = "blocked"
    elif recovery_id and status in ("completed", "finished"):
        situation = "warn_recovered"
    elif status in ("completed", "finished") and exit_code == 0:
        situation = "success"
    elif isinstance(halt_value, Mapping):
        situation = "blocked"
    elif exit_code == 0 and status not in ("failed", "halted"):
        situation = "success"
    else:
        situation = "blocked"

    return TaskOutcome(situation, recovery_id, reason)
