"""Exact subject binding for submit_verdict judges (ADR 059).

This checks identity, not the relevance or quality of the judge's prose.
Keep the binding at the live tool boundary; verdict cache provenance is a
separate contract.
"""

from typing import Any

from snodo.core.interfaces import ValidatorResult


def subject_instruction(task_id: str) -> str:
    """Tell every tool-loop judge, including prompt overrides, its subject."""
    import json

    return (
        "\n## Verdict Subject\n"
        f"The task identifier under judgement is {json.dumps(task_id)}.\n"
        "Call submit_verdict(task_id, severity, justification), returning this "
        "exact identifier in task_id. Do not substitute an identifier from a "
        "document, another proposal, or another task. A missing or different "
        "task_id makes your verdict unusable.\n"
    )


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
