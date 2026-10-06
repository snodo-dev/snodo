"""Pure operator guidance for canonical task halt outcomes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


HALT_OUTCOMES = frozenset(
    {"escalate", "blocker", "validator_error", "environment_error", "internal_error"}
)


@dataclass(frozen=True)
class NextAction:
    """An ordered operator action and its optional ready-to-run command."""

    instruction: str
    command: str | None = None


def next_actions_for_halt(
    halt: Mapping[str, Any], *, in_plan: bool = False
) -> tuple[NextAction, ...]:
    """Map a halt record to documented recovery steps for its task.

    The record is expected to carry ``final_decision`` (or ``halt_type``) and
    ``task_id``. Unknown outcomes fail closed rather than inventing guidance.
    """
    outcome = halt.get("final_decision") or halt.get("halt_type")
    task_id = halt.get("task_id")
    if outcome not in HALT_OUTCOMES:
        raise ValueError(f"unsupported halt outcome: {outcome!r}")
    if not isinstance(task_id, str) or not task_id:
        raise ValueError("halt record must include a task_id")

    if outcome == "escalate":
        return (
            NextAction("Have a human review and authorize the task.", f"snodo authorize {task_id}"),
        )
    if outcome == "blocker":
        if in_plan:
            return (NextAction("Fix the task forward within the same plan; replace its spec and rerun the wave if the specification is wrong."),)
        return (
            NextAction("Fix the specification or code, then retry the task.", f"snodo run --retry {task_id} --append-spec '<guidance>'"),
        )
    if outcome == "validator_error":
        return (
            NextAction("Repair the validator or provider problem, then retry the same task.", f"snodo run --retry {task_id}"),
        )
    if outcome == "environment_error":
        return (
            NextAction("Repair the execution environment, then retry the task with its unchanged spec.", f"snodo run --retry {task_id}"),
        )
    return (
        NextAction("Diagnose and repair the engine fault, then retry the task with its original spec.", f"snodo run --retry {task_id}"),
    )
