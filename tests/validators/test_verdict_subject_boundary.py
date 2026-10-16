"""Subject checking precedes interpretation of a live judge's findings."""

import json
from types import SimpleNamespace

import pytest

from snodo.compiler.models import Validator
from snodo.validators.llm_validator import LLMValidator


def _call(arguments):
    return SimpleNamespace(function=SimpleNamespace(
        name="submit_verdict", arguments=json.dumps(arguments),
    ))


@pytest.mark.parametrize("subject", [None, "another-task", "TASK-951", "task-951 ", 951])
def test_wrong_subject_is_not_a_correctable_severity_error(subject):
    validator = LLMValidator(Validator(
        validator_id="security", validator_type="security", criteria=["Check safety"],
    ))
    result = validator._extract_submit_verdict([_call({
        "task_id": subject,
        "severity": "invalid",
        "justification": "criterion 1 checked",
        "tool_access_missing": {"criterion": "Check safety", "capability": "runtime"},
    })], expected_task_id="task-951")
    assert result is not None
    assert result.error
    assert result.severity == "blocker"
    assert result.validator_id == "security"
    assert "task-951" in result.justification
    assert result.tool_access_missing is None
    assert result.cited_criteria is None


def test_exact_subject_keeps_tool_access_report():
    validator = LLMValidator(Validator(
        validator_id="security", validator_type="security", criteria=["Check safety"],
    ))
    missing = {"criterion": "Check safety", "capability": "runtime"}
    result = validator._extract_submit_verdict([_call({
        "task_id": "task-951", "severity": "warn",
        "justification": "Runtime cannot be inspected", "tool_access_missing": missing,
    })], expected_task_id="task-951")
    assert result is not None
    assert not result.error
    assert result.severity == "warn"
    assert result.justification == "Runtime cannot be inspected"
    assert result.tool_access_missing == missing
