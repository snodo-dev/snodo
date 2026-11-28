"""Exact subject binding for submit_verdict judges (ADR 059).

This checks identity, not the relevance or quality of the judge's prose.
Keep the binding at the live tool boundary; verdict cache provenance is a
separate contract.
"""

from typing import Any

from snodo.core.interfaces import ValidatorResult


def subject_instruction(task_id: str) -> str:
    """Tell every tool-loop judge, including prompt overrides, its subject."""
    return (
        "\n## Verdict Subject\n"
        "The task identifier under judgement is:\n"
        f"{task_id}\n"
        "Call submit_verdict(task_id, severity, justification), returning this "
        "exact identifier in task_id. Do not substitute an identifier from a "
        "document, another proposal, or another task. A missing or different "
        "task_id makes your verdict unusable.\n"
    )


def subject_id_from_instruction(instruction: str) -> str | None:
    """Read the exact task ID from the verdict-subject instruction."""
    marker = "The task identifier under judgement is:\n"
    start = instruction.rfind(marker)
    if start < 0:
        return None
    value_start = start + len(marker)
    end = instruction.find("\n", value_start)
    if end < 0:
        return None
    return instruction[value_start:end]


def reject_wrong_subject(
    arguments: Any, expected_task_id: str, validator_id: str,
) -> ValidatorResult | None:
    """Return the ordinary fail-closed error for an unbound verdict.

    Do not carry tool_access_missing or findings from a rejected verdict:
    those would give one unusable answer a second, unrelated interpretation.
    """
    if isinstance(arguments, dict) and "task_id" in arguments:
        actual = arguments["task_id"]
        if isinstance(actual, str) and actual == expected_task_id:
            return None
        reason = f"task_id {actual!r} does not exactly match {expected_task_id!r}"
    else:
        reason = f"missing task_id (expected {expected_task_id!r})"
    return ValidatorResult(
        validator_id=validator_id,
        severity="blocker",
        error=True,
        justification=f"Unusable submit_verdict: {reason}. Fail-closed.",
    )
