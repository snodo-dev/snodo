"""Traceback logging for LLM validator operational faults."""

import json
from unittest.mock import MagicMock


from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.validators.context import ValidatorContext
from snodo.validators.llm_validator import LLMValidator


def _known_failing_helper():
    value = None
    return value[0]


def _raising_completion(**kwargs):
    _known_failing_helper()


def _response():
    response = MagicMock()
    response.choices = [MagicMock()]
    response.choices[0].message.content = json.dumps({"severity": "pass", "justification": "ok"})
    return response


def _validator(*, tools=None):
    return Validator(
        validator_id="architecture", validator_type="architecture",
        evaluation_phase="pre_execute", criteria=[], tools=tools or [],
    )


def _assert_logged_traceback(caplog):
    record = next(record for record in caplog.records if "operational fault" in record.message)
    assert record.exc_info is not None
    assert "_known_failing_helper" in "".join(__import__("traceback").format_exception(*record.exc_info))


def test_single_shot_fault_logs_the_traceback(caplog, monkeypatch):
    monkeypatch.setattr("snodo.validators.llm_validator.supports_response_schema", lambda model: False)
    result = LLMValidator(_validator(), _raising_completion, model="gpt-4").evaluate(
        Task(id="task-1", spec="test")
    )
    _assert_logged_traceback(caplog)
    assert result.error is True


def test_tool_loop_fault_logs_the_traceback(caplog):
    spec = _validator(tools=["read_file"])
    context = ValidatorContext(
        task=Task(id="task-1", spec="test"), completion_fn=_raising_completion,
        model="gpt-4", workspace_mcp=object(), phase="post_execute",
    )
    result = LLMValidator(spec, model="gpt-4").evaluate(context)
    _assert_logged_traceback(caplog)
    assert result.error is True


def test_fault_justification_is_unchanged_by_the_traceback(caplog, monkeypatch):
    monkeypatch.setattr("snodo.validators.llm_validator.supports_response_schema", lambda model: False)
    result = LLMValidator(_validator(), _raising_completion, model="gpt-4").evaluate(
        Task(id="task-1", spec="test")
    )
    _assert_logged_traceback(caplog)
    assert result.justification == (
        "LLM validation failed due to operational error (TypeError): 'NoneType' object is not subscriptable"
    )
    assert "Traceback" not in result.justification
    assert result.severity == "blocker"
    assert result.error is True
