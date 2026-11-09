"""Traceback logging at the validator isolation boundary."""

import traceback
from unittest.mock import MagicMock

from snodo.compiler.models import Validator
from snodo.core.interfaces import Task
from snodo.validators.context import ValidatorBase
from snodo.validators.runner import run_validators


def _known_failing_helper():
    value = None
    return value[0]


class _RaisingValidator(ValidatorBase):
    @classmethod
    def registered_type(cls):
        return "traceback_test"

    def __init__(self, validator_spec):
        self.validator_spec = validator_spec

    def evaluate(self, context):
        _known_failing_helper()


def _validator():
    return Validator(
        validator_id="faulty",
        validator_type="traceback_test",
        evaluation_phase="pre_execute",
        criteria=["test"],
    )


def _run(monkeypatch):
    from snodo.validators.registry import _default_registry

    monkeypatch.setattr(_default_registry, "lookup", lambda validator_type: _RaisingValidator)
    validator = _validator()
    protocol = MagicMock()
    protocol.get_mode.return_value = MagicMock(
        name="test", tools=[], transitions={}, validators=[validator.validator_id]
    )
    results, _ = run_validators(
        protocol=protocol,
        validators=[validator],
        task=Task(id="task-1", spec="test"),
        current_mode="test",
        validator_config=MagicMock(max_tokens=1500, max_tool_turns=6),
    )
    return results[0]


def _assert_logged_traceback(caplog):
    record = next(record for record in caplog.records if "faulty" in record.message)
    assert record.exc_info is not None
    assert "_known_failing_helper" in "".join(traceback.format_exception(*record.exc_info))


def test_isolation_boundary_logs_the_traceback(caplog, monkeypatch):
    result = _run(monkeypatch)

    _assert_logged_traceback(caplog)
    assert result.error is True


def test_isolation_boundary_justification_is_unchanged(caplog, monkeypatch):
    result = _run(monkeypatch)

    _assert_logged_traceback(caplog)
    assert result.justification == (
        "Validator error (TypeError): 'NoneType' object is not subscriptable"
    )
    assert "Traceback" not in result.justification
    assert result.severity == "blocker"
    assert result.error is True
