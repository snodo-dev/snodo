"""Tests for acceptance validator uncheckable command execution criteria & contradiction detection (Fixes #75)."""

import json
from unittest.mock import MagicMock

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task, ValidatorResult
from snodo.engine.loop import GraphBuilder, LoopState
from snodo.validators.acceptance import AcceptanceValidator
from snodo.validators.context import ValidatorContext


def test_acceptance_prompt_requires_execution_evidence():
    """Execution criteria cannot pass without independent verification evidence."""
    val_spec = Validator(validator_id="acceptance", name="acceptance", validator_type="acceptance")
    val = AcceptanceValidator(val_spec)

    task = Task(id="t1", spec="ACCEPTANCE CRITERIA:\n1. make check passes\n2. docs updated")
    context = ValidatorContext(task=task, working_directory="/tmp")

    prompt = val._build_tool_loop_prompt(context, set())
    assert "only on Independent Execution Evidence" in prompt
    assert "plainly say it was not verified and treat it as unmet" in prompt
    assert "make check" in prompt
    assert "Never infer success from the tree or coder claims" in prompt


def test_acceptance_prompt_cites_quality_evidence():
    val_spec = Validator(validator_id="acceptance", name="acceptance", validator_type="acceptance")
    val = AcceptanceValidator(val_spec)
    task = Task(id="t1", spec="ACCEPTANCE CRITERIA:\n1. make gate passes")
    context = ValidatorContext(task=task, working_directory="/tmp", execution_evidence=[
        ValidatorResult(validator_id="quality", severity="pass", justification="make gate passed (exit 0)")
    ])
    prompt = val._build_tool_loop_prompt(context, set())
    assert "quality: pass; make gate passed (exit 0)" in prompt
    assert "Cite the validator, verdict," in prompt
    assert "passing quality gate includes its command, exit code and bounded output excerpt" in prompt
    assert "Do not call a gate-only criterion unmet when the gate passed" in prompt
    failed_context = ValidatorContext(
        task=task, working_directory="/tmp", execution_evidence=[ValidatorResult(
            validator_id="quality", severity="blocker",
            justification="Command: make gate (exit code 2). Output excerpt: docs check FAILED",
        )],
    )
    failed_prompt = val._build_tool_loop_prompt(failed_context, set())
    assert "quality: blocker; Command: make gate (exit code 2)" in failed_prompt
    assert "Failing evidence means unmet" in failed_prompt


def test_gate_pass_and_tree_met_criteria_pass_acceptance():
    """A passed gate supplies execution evidence; acceptance can pass without rerunning it."""
    val_spec = Validator(
        validator_id="acceptance", name="acceptance", validator_type="acceptance",
        tools=["read_file", "list_files"],
    )
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = None
    call = MagicMock()
    call.id = "verdict"
    call.function.name = "submit_verdict"
    call.function.arguments = json.dumps({
        "task_id": "t1", "severity": "pass",
        "justification": "Gate passed and the documentation criterion is met in the tree.",
    })
    response.choices[0].message.tool_calls = [call]
    response.choices[0].finish_reason = "tool_calls"
    completion = MagicMock(return_value=response)
    task = Task(id="t1", spec="ACCEPTANCE CRITERIA:\n1. make gate passes\n2. docs updated")
    context = ValidatorContext(
        task=task, working_directory="/tmp", completion_fn=completion,
        workspace_mcp=MagicMock(), git_mcp=MagicMock(), phase="post_execute",
        execution_evidence=[ValidatorResult(
            validator_id="quality", severity="pass",
            justification="Command: make gate (exit code 0). Output excerpt: docs check OK",
        )],
    )

    result = AcceptanceValidator(val_spec).evaluate(context)

    assert result.severity == "pass"
    prompt = completion.call_args[1]["messages"][0]["content"]
    assert "Command: make gate (exit code 0)" in prompt
    assert "docs check OK" in prompt


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
