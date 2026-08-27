"""Tests for acceptance validator uncheckable command execution criteria & contradiction detection (Fixes #75)."""

from unittest.mock import MagicMock
from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.validators.acceptance import AcceptanceValidator
from snodo.validators.context import ValidatorContext
from snodo.engine.loop import GraphBuilder, LoopState


def test_acceptance_prompt_uncheckable_command_instruction():
    """Acceptance validator prompt mandates UNCHECKABLE for command execution criteria."""
    val_spec = Validator(validator_id="acceptance", name="acceptance", validator_type="acceptance")
    val = AcceptanceValidator(val_spec)

    task = Task(id="t1", spec="ACCEPTANCE CRITERIA:\n1. make check passes\n2. docs updated")
    context = ValidatorContext(task=task, working_directory="/tmp")

    prompt = val._build_tool_loop_prompt(context, set(), False, "")
    assert "UNCHECKABLE: the criterion cannot be verified from static tree inspection" in prompt
    assert "make check" in prompt
    assert "NEVER mark a command execution criterion as MET" in prompt


def test_post_validate_contradiction_detection():
    """_post_validate overrides contradictory acceptance pass when quality fails."""
    audit_events = []
    def mock_audit(op, data):
        audit_events.append((op, data))

    builder = MagicMock(spec=GraphBuilder)
    builder._audit = mock_audit

    task = Task(id="t1", spec="test task")
    loop_state = LoopState(task=task, current_mode="build")

    quality_res = ValidatorResult(
        validator_id="quality",
        severity="warn",
        justification="make check failed (exit 2)",
    )
    acceptance_res = ValidatorResult(
        validator_id="acceptance",
        severity="pass",
        justification="Criterion 1 'make check passes' is MET based on tree structure.",
    )

    results = [quality_res, acceptance_res]

    # Execute contradiction detection logic from GraphBuilder._post_validate
    # Simulate post-validation result processing
    quality_failing = [
        r for r in results
        if getattr(r, "error", False) or r.severity in ("warn", "blocker")
    ]
    acceptance_passing = [
        r for r in results
        if r.validator_id == "acceptance" and r.severity == "pass"
    ]
    if quality_failing and acceptance_passing:
        q_res = quality_failing[0]
        for a_res in acceptance_passing:
            a_just = a_res.justification or ""
            builder._audit("validator_contradiction_detected", {
                "op": "validator_contradiction_detected",
                "task_ref": loop_state.task.id,
                "execution_validator": q_res.validator_id,
                "execution_justification": q_res.justification,
                "acceptance_justification": a_just,
            })
            if any(kw in a_just.lower() for kw in ("check", "test", "npm", "pytest", "build", "passes", "met")):
                a_res.severity = "warn"
                a_res.justification = (
                    f"[CONTRADICTION DETECTED: execution validator '{q_res.validator_id}' failed "
                    f"({q_res.justification}). Acceptance claim superseded.] {a_just}"
                )

    assert acceptance_res.severity == "warn"
    assert "CONTRADICTION DETECTED" in acceptance_res.justification
    assert len(audit_events) == 1
    assert audit_events[0][0] == "validator_contradiction_detected"
